"""Real, fixed-size DDP worker. Imports torch only when explicitly executed.

GPT-2 is randomly initialized from the same configuration as A1: no weights or
datasets are downloaded. The tiny model is an explicitly labelled smoke test.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import signal
import sys
import time
import traceback

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_demo.common import append_event, atomic_json, read_json
from agent_demo.ddp_backend import _process_identity


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--preflight", action="store_true")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    result.add_argument("--model", choices=("gpt2", "tiny"), default="gpt2")
    result.add_argument("--steps", type=int, default=20)
    result.add_argument("--checkpoint-every", type=int, default=5)
    result.add_argument("--sequence-length", type=int, default=128)
    result.add_argument("--epoch", type=int, default=0)
    result.add_argument("--seed", type=int, default=20261103)
    result.add_argument("--timeout", type=float, default=180)
    result.add_argument("--fault-gate-step", type=int)
    result.add_argument("--resume-manifest", type=Path)
    result.add_argument("--step-pause", type=float, default=.05)
    return result


def preflight(args):
    import torch
    facts = {"torch": torch.__version__, "python": sys.version.split()[0],
             "device": args.device, "model": args.model,
             "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
             "source": "MEASURED", "devices": [], "disk_free_bytes": shutil.disk_usage(args.output).free}
    if args.model == "gpt2":
        import transformers
        from transformers import GPT2Config, GPT2LMHeadModel  # noqa: F401
        facts["transformers"] = transformers.__version__
        # Two retained checkpoints plus an in-progress commit need disk headroom.
        if facts["disk_free_bytes"] < 6 * 1024**3:
            raise RuntimeError("GPT-2 checkpoints require at least 6 GiB free in --output; choose a shared data disk")
    if args.device == "cuda":
        import torch.distributed as dist
        if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
            raise RuntimeError("At least two authorized visible CUDA devices are required")
        slots = json.loads(os.environ["FABRIC_GPU_SLOTS"])
        if torch.cuda.device_count() != len(slots):
            raise RuntimeError("CUDA visible-device count differs from the authorized pool; check every GPU identifier")
        if not dist.is_available() or not dist.is_nccl_available():
            raise RuntimeError("This CUDA experiment requires a PyTorch build with NCCL")
        for index in range(torch.cuda.device_count()):
            free, total = torch.cuda.mem_get_info(index)
            props = torch.cuda.get_device_properties(index)
            facts["devices"].append({"logical_id": index, "slot_id": slots[index], "name": props.name,
                                     "free_bytes": free, "total_bytes": total,
                                     "uuid": str(getattr(props, "uuid", "unavailable"))})
            if args.model == "gpt2" and free < 5 * 1024**3:
                raise RuntimeError(f"Visible GPU {index} has less than 5 GiB free; use an exclusively allocated pool")
    atomic_json(args.output / "preflight.json", facts)
    print(json.dumps(facts, ensure_ascii=False))


def train(args):
    import torch
    import torch.distributed as dist
    from torch.nn.parallel import DistributedDataParallel

    rank = int(os.environ["RANK"])
    local_rank = int(os.environ["LOCAL_RANK"])
    world = int(os.environ["WORLD_SIZE"])
    if world != 2:
        raise ValueError("This experiment keeps world_size fixed at 2")
    epoch_dir = args.output / f"epoch-{args.epoch}"
    epoch_dir.mkdir(parents=True, exist_ok=True)
    rank_path = epoch_dir / f"rank-{rank}.json"
    event_path = epoch_dir / f"rank-{rank}.jsonl"
    slots = json.loads(os.environ.get("FABRIC_GPU_SLOTS", '["cpu0","cpu1"]'))
    base = {"pid": os.getpid(), "rank": rank, "local_rank": local_rank,
            "slot_id": slots[local_rank], "epoch": args.epoch, "world_size": world,
            "model": args.model, "device": args.device, "source": "MEASURED",
            "run_id": os.environ["FABRIC_RUN_ID"], "process_identity": _process_identity(os.getpid())}
    if base["process_identity"] is None:
        raise RuntimeError("Cannot establish worker OS process birth identity")
    state = {**base, "status": "initializing", "step": 0, "heartbeat": time.time(),
             "checkpoint_loaded": None, "gate_wait_seconds": 0.0}

    def publish(status=None, **values):
        if status:
            state["status"] = status
        state.update(values)
        state["heartbeat"] = time.time()
        atomic_json(rank_path, state)
        append_event(event_path, {**base, "status": state["status"], **values})

    def terminated(signum, _frame):
        # This observation is emitted by the worker, independently of the injector.
        event = {**base, "kind": "termination_signal", "signal": signum,
                 "observed_at": time.time(), "step": state.get("step", 0)}
        atomic_json(epoch_dir / f"exit-{rank}.json", event)
        publish("failed", error=f"termination signal {signum}")
        os._exit(128 + signum)

    signal.signal(signal.SIGTERM, terminated)
    publish()
    initialized = False
    try:
        device = torch.device("cuda", local_rank) if args.device == "cuda" else torch.device("cpu")
        if args.device == "cuda":
            torch.cuda.set_device(local_rank)
            props = torch.cuda.get_device_properties(local_rank)
            state["device_uuid"] = str(getattr(props, "uuid", "unavailable"))
            state["device_name"] = props.name
            state["mapping_verification_level"] = "uuid_observed" if state["device_uuid"] != "unavailable" else "logical_only"
        torch.manual_seed(args.seed)
        random.seed(args.seed)
        dist.init_process_group("nccl" if args.device == "cuda" else "gloo",
                                timeout=timedelta(seconds=args.timeout))
        initialized = True
        if args.model == "gpt2":
            from transformers import GPT2Config, GPT2LMHeadModel
            config = GPT2Config(vocab_size=50257, n_positions=1024, n_ctx=1024,
                                n_embd=768, n_layer=12, n_head=12, n_inner=3072,
                                tie_word_embeddings=True, use_cache=False)
            model = GPT2LMHeadModel(config)
            if sum(p.numel() for p in model.parameters()) != 124439808:
                raise RuntimeError("GPT-2 parameter count differs from A1 fixed configuration")
        else:
            model = torch.nn.Sequential(torch.nn.Linear(32, 64), torch.nn.ReLU(), torch.nn.Linear(64, 16))
        model = model.to(device=device, dtype=torch.float32).train()
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4, foreach=False)
        ddp = DistributedDataParallel(model, device_ids=[local_rank] if args.device == "cuda" else None)
        start_step = 0
        loaded = None
        if args.resume_manifest:
            manifest = read_json(args.resume_manifest)
            if not manifest or manifest.get("world_size") != world or manifest.get("model") != args.model:
                raise RuntimeError("Checkpoint manifest is missing or incompatible")
            checkpoint_path = args.output / manifest["file"]
            if file_hash(checkpoint_path) != manifest["sha256"]:
                raise RuntimeError("Checkpoint content hash mismatch")
            checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
            if (checkpoint["world_size"] != world or checkpoint["model_name"] != args.model
                    or checkpoint["sequence_length"] != args.sequence_length):
                raise RuntimeError("Checkpoint configuration mismatch")
            model.load_state_dict(checkpoint["model"])
            optimizer.load_state_dict(checkpoint["optimizer"])
            start_step = int(checkpoint["step"])
            rng = checkpoint["rng_states"][rank]
            torch.set_rng_state(rng["torch_cpu"])
            random.setstate(rng["python"])
            if args.device == "cuda":
                torch.cuda.set_rng_state(rng["torch_cuda"], device)
            loaded = {"step": start_step, "sha256": manifest["sha256"], "file": manifest["file"]}
        else:
            # Distinct dropout/input streams, with common initial model parameters.
            torch.manual_seed(args.seed + rank)
            random.seed(args.seed + rank)
        publish("running", step=start_step, checkpoint_loaded=loaded,
                parameter_count=sum(p.numel() for p in model.parameters()))
        dist.barrier()

        def commit_checkpoint(step):
            rng = {"torch_cpu": torch.get_rng_state(), "python": random.getstate()}
            if args.device == "cuda":
                rng["torch_cuda"] = torch.cuda.get_rng_state(device).cpu()
            states = [None] * world
            dist.all_gather_object(states, rng)
            publish("checkpointing", step=step)
            outcome = [None]
            if rank == 0:
                try:
                    folder = args.output / "checkpoints"
                    folder.mkdir(exist_ok=True)
                    name = f"checkpoint-e{args.epoch}-s{step}.pt"
                    target = folder / name
                    temp = target.with_suffix(".tmp")
                    # Saving CUDA tensors is supported; loading always maps to CPU.
                    with temp.open("wb") as handle:
                        torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(),
                                    "step": step, "world_size": world, "model_name": args.model,
                                    "sequence_length": args.sequence_length, "rng_states": states}, handle)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp, target)
                    manifest = {"file": str(target.relative_to(args.output)), "sha256": file_hash(target),
                                "step": step, "epoch": args.epoch, "world_size": world,
                                "model": args.model, "sequence_length": args.sequence_length,
                                "committed_at": time.time(), "size_bytes": target.stat().st_size}
                    # Publish the manifest only after the versioned file is complete.
                    atomic_json(args.output / "checkpoint_manifest.json", manifest)
                    atomic_json(folder / f"manifest-e{args.epoch}-s{step}.json", manifest)
                    outcome[0] = {"ok": True, "manifest": manifest}
                    # This is an evidence-producing experiment: retain every
                    # committed checkpoint, including the exact recovery source.
                    # Lightweight handoff archives exclude the .pt payloads.
                except Exception as exc:
                    outcome[0] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            dist.broadcast_object_list(outcome, src=0)
            if not outcome[0]["ok"]:
                raise RuntimeError(outcome[0]["error"])
            dist.barrier()
            publish("running", step=step, committed_checkpoint_step=step,
                    committed_checkpoint_sha256=outcome[0]["manifest"]["sha256"])

        for step in range(start_step + 1, args.steps + 1):
            begun = time.perf_counter()
            generator = torch.Generator(device="cpu").manual_seed(args.seed + step * 1009 + rank)
            optimizer.zero_grad(set_to_none=True)
            if args.model == "gpt2":
                inputs = torch.randint(0, 50257, (1, args.sequence_length), generator=generator).to(device)
                loss = ddp(input_ids=inputs, labels=inputs).loss
            else:
                inputs = torch.randn((8, 32), generator=generator).to(device)
                targets = torch.randn((8, 16), generator=generator).to(device)
                loss = torch.nn.functional.mse_loss(ddp(inputs), targets)
            if not bool(torch.isfinite(loss).item()):
                raise RuntimeError("Non-finite loss")
            loss.backward()
            optimizer.step()
            if args.device == "cuda":
                torch.cuda.synchronize()
            duration = (time.perf_counter() - begun) * 1000
            # Explicit barrier makes committed logical progress unambiguous.
            dist.barrier()
            publish("running", step=step, loss=float(loss.item()), train_step_ms=duration)
            if step % args.checkpoint_every == 0 or step == args.steps:
                commit_checkpoint(step)
            if args.epoch == 0 and args.fault_gate_step == step:
                gate_started = time.monotonic()
                publish("experiment_gate", step=step,
                        gate_note="Driver synchronization; excluded from train_step_ms")
                release = args.output / "release_gate.json"
                while not release.exists():
                    elapsed = time.monotonic() - gate_started
                    if elapsed > args.timeout:
                        raise TimeoutError("Experiment driver did not release the fault gate")
                    publish("experiment_gate", step=step, gate_wait_seconds=elapsed)
                    time.sleep(.1)
                publish("running", gate_wait_seconds=time.monotonic() - gate_started)
            time.sleep(args.step_pause)
        publish("completed", step=args.steps, completed_at=time.time())
        dist.barrier()
    except BaseException as exc:
        atomic_json(epoch_dir / f"exit-{rank}.json", {**base, "kind": "worker_exception",
                    "observed_at": time.time(), "step": state.get("step", 0),
                    "error": f"{type(exc).__name__}: {exc}"})
        publish("failed", error=f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise
    finally:
        if initialized:
            dist.destroy_process_group()


def main():
    args = parser().parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if not 2 <= args.steps <= 10000 or not 1 <= args.checkpoint_every <= args.steps:
        raise ValueError("Invalid training step/checkpoint configuration")
    if not 2 <= args.sequence_length <= 1024 or not 1 <= args.timeout <= 1800:
        raise ValueError("Invalid sequence length or timeout")
    if args.preflight:
        preflight(args)
    else:
        train(args)


if __name__ == "__main__":
    main()
