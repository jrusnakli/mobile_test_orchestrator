import subprocess
import os.path

path_args = "/path_to/args.txt"
dir_path = os.path.dirname(os.path.realpath(__file__))
print(dir_path)
for i in range(3):
    subprocess.Popen(["python", dir_path+"/" + "workerserver.py", path_args,
                      f"555{4 + i}", f"emulator-555{4 + i}"])