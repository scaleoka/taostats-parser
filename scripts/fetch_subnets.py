import subprocess
import json

print("Запрашиваем список подсетей через btcli...\n")

result = subprocess.run(
    ['python', '-m', 'btcli', 'subnets', 'list', '--json'],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

if result.returncode == 0:
    data = json.loads(result.stdout)
    for subnet in data:
        print(f"Subnet {subnet['netuid']}: {subnet['name']}")
else:
    print("Ошибка:", result.stderr)
