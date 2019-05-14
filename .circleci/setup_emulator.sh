#echo "Downloading emulator image..."
#yes | ${ANDROID_SDK_ROOT}/tools/bin/sdkmanager "system-images;android-25;google_apis;arm64-v8a"
#echo "Downloaded.  Creating avd..."
#test -d ${ANDROID_AVD_HOME}/MTO_emulator.avd ||
#${ANDROID_SDK_ROOT}/tools/bin/avdmanager create avd --force -p ${ANDROID_AVD_HOME} -n MTO_emulator -k "system-images;android-25;google_apis;arm64-v8a" -d "pixel_xl"
#echo "Created @MTO_emulator"
#exit 0