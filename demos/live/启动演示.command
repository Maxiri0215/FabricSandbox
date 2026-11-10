#!/bin/zsh
# FabricSandbox local demo; uses an existing Python, installs nothing.
set -eu
cd -- "$(dirname -- "$0")"

fabric_demo_python=""
for fabric_demo_candidate in python3.12 python3.11 python3.10 python3; do
  fabric_demo_resolved="$(command -v "$fabric_demo_candidate" 2>/dev/null || true)"
  [[ -n "$fabric_demo_resolved" ]] || continue
  if "$fabric_demo_resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    fabric_demo_python="$fabric_demo_resolved"
    break
  fi
done

if [[ -z "$fabric_demo_python" ]]; then
  print -u2 '未找到 Python 3.10 或更新版本。本脚本不会自动下载或安装软件。'
  print -u2 '请用已有的 Python 3.10+ 执行 server.py --port 0 --open。'
  read -r '?按回车关闭窗口。'
  exit 1
fi

print 'FabricSandbox 本地演示'
print "Python: $fabric_demo_python"
print '即将自动打开浏览器。服务会选择空闲端口，请保留此终端。'
print '演示结束：先点网页中的“停止实验”，再在此处按 Control+C。'
exec "$fabric_demo_python" server.py --port 0 --open
