import subprocess

def f():
    return subprocess.run(['rsync', '-a', 'a', 'b'], check=True)
