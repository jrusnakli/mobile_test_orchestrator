#!/usr/bin/env python3

import os
import random
import setuptools
import shutil
import subprocess
import sys


BUTLER_SERVICE_SRC_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), "..", "testbutlerservice")
APK_RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "src", "androidtestorchestrator", "resources", "apks")
if not os.path.isdir(APK_RESOURCES_DIR):
    os.makedirs(APK_RESOURCES_DIR)

keygen = shutil.which("keytool")

API_LEVEL = os.environ.get("ANDROID_API_LEVEL") or "28.0.3"

def add_ext(path):
    if sys.platform == 'win32':
        return path + ".exe"
    return path

zipalign = os.path.join(os.environ['ANDROID_SDK_ROOT'], "build-tools", API_LEVEL, add_ext("zipalign"))
apksigner = os.path.join(os.environ['ANDROID_SDK_ROOT'], "build-tools", API_LEVEL, "apksigner")

if not os.path.exists(zipalign):
    raise FileNotFoundError("Could not find zipalign tool under ANDROID_SDK_ROOT in %s" % zipalign)
if not os.path.exists(apksigner) and not os.path.exists(apksigner + ".bat"):
    raise FileNotFoundError("Could not find apksigner tool under ANDROID_SDK_ROOT in %s" % apksigner)
os.environ["ANDROID_HOME"] = os.environ.get("ANDROID_SDK_ROOT")


def generate_keystore(key_store_path, alias_name):
    if sys.platform == 'win32':
        import getpass
        return getpass.getpass(prompt="Password for keystore:")

    key = [c for c in __file__]
    random.shuffle(key)
    key = ''.join(key)
    cmd = [keygen, "-genkey", "-v", "-keystore", key_store_path, "-alias", alias_name,
           "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "--storepass", key,
           "--keypass", key,
           "-dname", "CN=linkedin.com, OU=ToolsPartners, O=LinkedIn, L=Rusnak, S=John, C=US"
          ]
    completed = subprocess.run(cmd)
    if completed.returncode != 0 or not os.path.exists(key_store_path):
        raise Exception("Failed to generate keystore for signing %s" % ' '.join(cmd))
    return key


def sign_apk(path_to_apk):
    import tempfile
    dir = tempfile.mkdtemp()
    try:
        alias_name = "testbutlerservice"
        if sys.platform == 'win32':
            key_store_path = os.environ.get("WINDOWS_KEY_STORE_PATH")
            if not key_store_path:
                raise Exception("On windows, must specify location of keystore in  $WINDOWS_KEY_STORE_PATH")
        else:
            key_store_path = os.path.join(dir, "keystore.keys")
        key = generate_keystore(key_store_path, alias_name)
        path_aligned_apk = path_to_apk.replace("-unsigned.apk", "-release.apk")
        if os.path.exists(path_aligned_apk):
            os.remove(path_aligned_apk)
        completed = subprocess.run([zipalign, "-p", "4", path_to_apk, path_aligned_apk])
        if completed.returncode != 0:
            raise Exception("Failed to zipalign apk")
        if sys.platform == 'win32':
            completed = subprocess.run([apksigner, "sign", "--ks", key_store_path, path_aligned_apk, ],
                                       input=key.encode('utf-8'),
                                       stderr=subprocess.PIPE,
                                       stdout=subprocess.PIPE,
                                       shell=True)
        else:
            completed = subprocess.run([apksigner, "sign", "--ks", key_store_path, path_aligned_apk, ],
                                       input=key.encode('utf-8'),
                                       stderr=subprocess.PIPE,
                                       stdout=subprocess.PIPE,
                                       )
        if completed.returncode != 0:
            raise Exception("Failed to sign apk %s" % completed.stderr)
        return path_aligned_apk
    finally:
        shutil.rmtree(dir)


def build_signed_app():
    my_env = os.environ.copy()
    my_env["DEX_PREOPT_DEFAULT"] = "nostripping"  # prevents over eager optimization that removes all code
    if sys.platform == 'win32':
        completed = subprocess.run(["gradlew", "assembleRelease"], cwd=BUTLER_SERVICE_SRC_DIR, env=my_env,
                                   shell=True)
    else:
        completed = subprocess.run(["./gradlew", "assembleRelease"], cwd=BUTLER_SERVICE_SRC_DIR, env=my_env)
    if completed.returncode != 0:
        raise Exception("Failed to build app")
    unsigned_apk = os.path.join(BUTLER_SERVICE_SRC_DIR, "app", "build", "outputs", "apk",
                                "release", "app-release-unsigned.apk")
    if not os.path.exists(unsigned_apk):
        raise Exception("Build succeeded but no unsigned apk at %s??" % unsigned_apk)
    release_apk = sign_apk(unsigned_apk)
    move_to = os.path.join(APK_RESOURCES_DIR, "TestButlerLive.apk")
    shutil.move(release_apk, move_to)
    return move_to


released_apk = build_signed_app()

if len(sys.argv) <= 1:
    print("Built release app in %s.  Nothing more requested" % released_apk)
    sys.exit(0)


setuptools.setup(
    name='androidtestorchestrator',
    version='1.0.0',
    package_dir={'': 'src'},
    packages=setuptools.find_packages('src'),
    include_package_data=True,
    entry_points={
    'console_scripts': [
    ]
  }
)
