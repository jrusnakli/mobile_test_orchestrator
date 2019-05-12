env
echo "LS######"
ls /usr/local/bin
echo "########"
yes | ${ANDROID_SDK_ROOT}/tools/bin/sdkmanager "system-images;android-28;default;x86_64"
${ANDROID_SDK_ROOT}/tools/bin/avdmanager create avd -n test -k "system-images;android-28;default;x86_64" -d "pixel_xl"
exit 0