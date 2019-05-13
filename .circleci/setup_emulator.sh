echo "Downloading emulator image..."
yes | ${ANDROID_SDK_ROOT}/tools/bin/sdkmanager "system-images;android-28;default;x86_64"
echo "Downloaded.  Creating avd..."
${ANDROID_SDK_ROOT}/tools/bin/avdmanager create avd -n MTO_emulator -k "system-images;android-28;default;x86_64" -d "pixel_xl"
echo "Created @MTO_emulator"
exit 0