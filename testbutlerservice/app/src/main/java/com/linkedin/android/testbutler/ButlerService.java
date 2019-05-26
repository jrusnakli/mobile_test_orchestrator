/**
 * Copyright (C) 2016 LinkedIn Corp.
 * <p>
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 * <p>
 * http://www.apache.org/licenses/LICENSE-2.0
 * <p>
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.linkedin.android.testbutler;

import android.app.IntentService;
import android.app.Notification;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.os.IBinder;
import android.os.RemoteException;
import android.provider.Settings;
import android.support.annotation.Nullable;
import android.support.v4.app.JobIntentService;
import android.util.Log;

import java.lang.InterruptedException;
import java.lang.reflect.Method;
import java.lang.reflect.Field;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.Locale;


/**
 * Main entry point into the Test Butler application.
 * <p>
 * Runs while instrumentation tests are running and handles modifying several system settings in order
 * to make test runs more reliable, as well as enable tests to modify system settings on the fly
 * to test application behavior under a given configuration.</p>
 * <p>
 * This implementation is for us in the mobile cloud, and operates in a manner simiar to the way
 * Instrumentation Andrdoi TestRunner communicates test status: via stdout over the Android device bridge
 * (adb) connection.  Here, the setting to change M communicated on stdout as a command that is to
 * be picked up by the server and effected.  The code will look for a change in the value before
 * returning control back to the client, or will timeout if the change doesn't happen in a timely manner.</p>
 */
@SuppressWarnings("deprecation")
public class ButlerService extends JobIntentService {
    private Intent mForegroundIntent = null;
    //for debug:
    private static final String TAG = CommandInvocation.TAG;

    // These should match declaration of intents in Manifest.xml
    final static String ACTION_CMD_RESPONSE = "com.linkedin.android.testbutler.COMMAND_RESPONSE";
    final static String ACTION_TEST_ONLY_SEND_CMD = "com.linkedin.android.testbutler.FOR_TEST_ONLY_SEND_CMD";
    final static String ACTION_SET_SYSTEM_LOCALE = "com.linkedin.android.testbutler.SET_SYSTEM_LOCALE";

    private final ButlerApi.Stub butlerApi = new ButlerApi.Stub() {

        // should match Python scripting on server side code (in mdl-integration MP):
        private static final String SETTING_PREFIX = "TEST_BUTLER_SETTING:";
        private static final String GRANT_PREFIX = "TEST_BUTLER_GRANT:";

        private static final String NS_GLOBAL = "global";
        private static final String NS_SECURE = "secure";
        private static final String NS_SYSTEM = "system";

        /**
         * @param namespace namespace of property being requested
         * @param key key name of property being requested
         * @return String value of property
         * @throws Settings.SettingNotFoundException
         */
        private String getStringProperty(final String namespace, final String key) throws Settings.SettingNotFoundException {

            String returnValue;
            if (namespace.equals(NS_GLOBAL)) {
                returnValue = Settings.Global.getString(getContentResolver(), key);
            } else if (namespace.equals(NS_SECURE)) {
                returnValue = Settings.Secure.getString(getContentResolver(), key);
            } else if (namespace.equals(NS_SYSTEM)) {
                returnValue = Settings.System.getString(getContentResolver(), key);
            } else {
                throw new Settings.SettingNotFoundException(
                        "Internal error: invalid namespace received on getting property: " + namespace);
            }
            return returnValue == null ? "" : returnValue;
        }

        /**
         *
         * @param namespace namespace of property being requested
         * @param key key name of property being requested
         * @return Integer property requested
         * @throws Settings.SettingNotFoundException
         */
        private Integer getIntProperty(final String namespace, final String key) throws Settings.SettingNotFoundException {
            if (key.equals("location_providers_allowed")) {
                return getIntProperty(namespace, "location_mode");
            } else if (namespace.equals(NS_GLOBAL)) {
                return Settings.Global.getInt(getContentResolver(), key);
            } else if (namespace.equals(NS_SECURE)) {
                return Settings.Secure.getInt(getContentResolver(), key);
            } else if (namespace.equals(NS_SYSTEM)) {
                return Settings.System.getInt(getContentResolver(), key);
            } else {
                throw new Settings.SettingNotFoundException(
                        "Internal error: invalid namespace received on getting property: " + namespace);
            }
        }

        /**
         * Invoke a command, wait for and return the response
         * @param cmd text of command
         * @return the CommandResponse from the host
         * @throws InterruptedException if interrupted while waiting for response
         * @throws ExecutionException on execution exception while waiting for response
         * @throws TimeoutException if response takes more than 1 second
         */
        private CommandResponse invoke(final String cmd) throws
                InterruptedException, ExecutionException, TimeoutException {
            Future<CommandResponse> futureResponse = CommandInvocation.invoke(cmd);

            CommandResponse response = futureResponse.get(1, TimeUnit.SECONDS);
            if (response.getStatusCode() != 0) {
                // log debug message, as server should handle as error/exception
                Log.d(TAG, "Server-side error granting permission: " + response.getMessage());
            }
            return response;
        }


        /**
         * Send a text command to set a property via stdout over adb to signal server to take action
         * Look for return
         * @param namespace Namespace of param to set
         * @param key  key name of param to set
         * @param value value to set parameter to
         * @return CommandResponse from host, or null if no property change was needed
         */
        private CommandResponse sendSetStringProperty(final String namespace, final String key,
                                              String value){
            try {
                if (getStringProperty(namespace, key).toLowerCase().equals(value.toLowerCase())) {
                    return null;
                }
                final String put_cmd = SETTING_PREFIX + " " + namespace + " " + key + " " + value + "\n";
                return invoke(put_cmd);
            } catch (InterruptedException| TimeoutException | ExecutionException e) {
                CommandInvocation.signalError("ERROR: Set of new system property " + namespace +
                        ":" + key + " failed: Interrupted: " + e.getMessage());
                return null;
            } catch (Settings.SettingNotFoundException snfe) {
                Log.e(TAG, "Setting not found!: " + namespace + ":" + key);
                return null;
            }
        }

        /**
         * Send a text command to set a integer property via stdout over adb to signal server to take action
         * @param namespace Namespace of param to set
         * @param key  key name of param to set
         * @param value value to set parameter to
         * @return CommandResponse from host, or null if no property change was needed
         */
        private CommandResponse sendSetIntProperty(final String namespace, final String key,
                                           int value) {
            try {
                if (getIntProperty(namespace, key).equals(value)) {
                    return null;
                }
                final String put_cmd = SETTING_PREFIX + " " + namespace + " " + key + " " + value + "\n";
                return invoke(put_cmd);
            } catch (InterruptedException| TimeoutException | ExecutionException e) {
                CommandInvocation.signalError("ERROR: Set of new system property " + namespace +
                        ":" + key + " failed: Interrupted: " + e.getMessage());
                return null;
            } catch (Settings.SettingNotFoundException snfe) {
                Log.e(TAG, "Setting not found!: " + namespace + ":" + key);
                return null;
            }
        }

        private boolean validateStringProperty(final String namespace, final String key, String expectedValue) {
            try {
                // signals server to invoke adb command to set property:
                return getStringProperty(namespace, key).toLowerCase().equals(expectedValue.toLowerCase());
            } catch (Settings.SettingNotFoundException e) {
                return false;
            }
        }

        private boolean validateIntProperty(final String namespace, final String key, int expectedValue) {
            try {
                // signals server to invoke adb command to set property:
                return getIntProperty(namespace, key).equals(expectedValue);
            } catch (Settings.SettingNotFoundException e) {
                return false;
            }
        }

        private void onError(String namespace, String key, String expectedValue, CommandResponse response){
            CommandInvocation.signalError("ERROR: Set of new system property " + namespace +
                    ":" + key + " to " + expectedValue + " failed");
            try {
                Log.d(TAG, "Value is now at: " + getStringProperty(namespace, key) +
                        " but looking for " + expectedValue.toLowerCase());
            } catch (Settings.SettingNotFoundException e) {
                // not log message to present
            }
            if (response != null) {
                Log.e(TAG, "Message from host: " + response.getMessage());
            }
        }

        private void onErrorInt(String namespace, String key, int expectedValue,
                             CommandResponse response){
            CommandInvocation.signalError("ERROR: Set of new system property " + namespace +
                    ":" + key + " to " + String.valueOf(expectedValue) + " failed");
            try {
                Log.d(TAG, "Value is now at: " + String.valueOf(getIntProperty(namespace, key)) +
                        " but looking for " + String.valueOf(expectedValue));
            } catch (Settings.SettingNotFoundException e) {
                // not log message to present
            }
            if (response != null) {
                Log.e(TAG, "Message from host: " + response.getMessage());
            } else {
                Log.e(TAG, "Response was null");
            }
        }

        /**
         * Request host to set the wifi state of device
         * @param enabled whether to enable or disable wifi
         * @return true if successfully set
         */
        @Override
        public boolean setWifiState(boolean enabled){
            CommandResponse response = sendSetIntProperty(NS_GLOBAL, "wifi_on", enabled ? 1 : 0);
            if (response == null || response.getStatusCode() != 0){
                onError(NS_GLOBAL,"wifi_on", enabled ? "1" : "0", response);
            }
            return validateIntProperty(NS_GLOBAL, "wifi_on", enabled? 1 : 0);
        }

        @Override
        public boolean setLocationMode(int locationMode)
                throws RemoteException {
            if (Settings.Secure.LOCATION_MODE_HIGH_ACCURACY == locationMode) {
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+gps");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+network");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+wifi");
            } else if (Settings.Secure.LOCATION_MODE_SENSORS_ONLY == locationMode) {
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+gps");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "-network");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "-wifi");
            } else if (Settings.Secure.LOCATION_MODE_BATTERY_SAVING == locationMode) {
                if(!getPackageManager().hasSystemFeature(PackageManager.FEATURE_LOCATION)){
                    throw new RemoteException("Location not supported on this device");
                }
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+network");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+wifi");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "-gps");
            } else if (Settings.Secure.LOCATION_MODE_OFF == locationMode) {
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "-gps");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "-network");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "-wifi");
            } else {
                CommandInvocation.signalError("ERROR: Invalid location mode value : " + locationMode);
                return false;
            }
            return validateIntProperty(NS_SECURE, "location_mode", locationMode);
        }

        @Override
        public boolean setRotation(int rotation) {
            CommandResponse response = sendSetIntProperty(NS_SYSTEM, "accelerometer_rotation", 0);
            Log.d(TAG, "ROTATION: " + response);
            if (response == null || response.getStatusCode() != 0){
                onErrorInt(NS_SYSTEM, "accelerometer_rotation", 0, response);
            }
            response = sendSetIntProperty(NS_SYSTEM, "user_rotation", rotation);
            if (response == null || response.getStatusCode() != 0){
                onErrorInt(NS_SYSTEM, "user_rotation", rotation, response);
            }
            return validateIntProperty(NS_SYSTEM, "accelerometer_rotation", 0) &&
                validateIntProperty(NS_SYSTEM, "user_rotation", rotation);
        }

        @Override
        public boolean setGsmState(boolean enabled) {
            return false;
        }

        @Override
        public boolean grantPermission(String packageName, String permission) {
            int hasPerm = getApplicationContext().getPackageManager().checkPermission(permission,
                    packageName);
            if (hasPerm == PackageManager.PERMISSION_GRANTED) {
                Log.i(TAG, "Already granted: permission " + permission + " to pacakge " + packageName);
                return true;
            }
            final String cmd = GRANT_PREFIX + " permission " + packageName + " " + permission;
            try{
                invoke(cmd);
            } catch (InterruptedException | ExecutionException |TimeoutException e) {
                CommandInvocation.signalError("ERROR: Execution error granting permission: " +
                        e.getMessage());
                return false;
            }
           return getApplicationContext().getPackageManager().checkPermission(permission, packageName) ==
                   PackageManager.PERMISSION_GRANTED;
        }

        @Override
        public boolean setSpellCheckerState(boolean enabled) {
            return false;
        }

        @Override
        public boolean setShowImeWithHardKeyboardState(boolean enabled) {
            return false;
        }

        @Override
        public boolean setImmersiveModeConfirmation(boolean enabled) {
            CommandResponse response = sendSetStringProperty(NS_SECURE, "immersive_mode_confirmation",
				      enabled ? "\"\"\"\"" : "confirmed");
            if (response == null || response.getStatusCode() != 0){
                onError(NS_SECURE, "immersive_mode_confirmation", enabled ? "" : "confirmed", response);
            }
            return validateStringProperty(NS_SECURE, "immersive_mode_confirmation", enabled ? "" : "confirmed");
        }

    };

    public ButlerService() {
        super();
    }

    @Override
    public void onCreate() {
        super.onCreate();
        Log.d(TAG, "MDC ButlerService starting up...");
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        Log.d(TAG, "MDC ButlerService shutting down...");
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return butlerApi;
    }

    @Nullable
    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        super.onStartCommand(intent, flags, startId);
        onHandleIntent(intent);
        return android.app.Service.START_STICKY;
    }

    @Override
    protected void onHandleWork(Intent intent) {
        onHandleIntent(intent);
    }

    protected void onHandleIntent(Intent intent){
        /**
         * A test-only intent ACTION_TEST_ONLY_SEND_CMD is provided for use in testing
         * this TestButler code (only to be used for that purpose).  This provides
         * an easy mechanism to test the very fundamentals of interaction between
         * TestButler running on a device and the server to which the device is connected.
         * It eliminates the complexity of needing a third component (an explicit test app)
         * that could make debugging the basics far more difficult.
         */
        if (intent.getAction() == null){
            return;
        }
        if (intent.getAction().equals(ACTION_CMD_RESPONSE)) {
            final String response = intent.getStringExtra("response");
            try {
                CommandInvocation.processServerResponse(response);
            } catch (Exception e){
                CommandInvocation.signalError("ERROR: exception encountered while handling command response " +
                                               e.getMessage());
            }
        } else if (intent.getAction().equals(ACTION_TEST_ONLY_SEND_CMD)){
            // This is only for test purposes to send a command over to server
            // and allow server to test response logic
            Log.d(TAG, "Sending command \"" + intent.getStringExtra("command") + "\"");
            CommandInvocation.invoke("TEST_ONLY " + intent.getStringExtra("command"));

        } else if (intent.getAction().equals(ACTION_SET_SYSTEM_LOCALE)) {
            Log.d(TAG, "Setting system locale");
            Log.d(TAG, intent.getStringExtra("locale"));
            Locale locale = new Locale(intent.getStringExtra("locale"));

            /**
             * There is very fuzzy documentation on how to set the system (device global) local
             * setting.  Many solutions only change it for the current app, which is not what
             * we want.
             */
            try {
                Class amnClass = Class.forName("android.app.ActivityManagerNative");
                Object amn = null;
                Configuration config = null;

                // amn = ActivityManagerNative.getDefault();
                Method methodGetDefault = amnClass.getMethod("getDefault");
                methodGetDefault.setAccessible(true);
                amn = methodGetDefault.invoke(amnClass);

                // config = amn.getConfiguration();
                Method methodGetConfiguration = amnClass.getMethod("getConfiguration");
                methodGetConfiguration.setAccessible(true);
                config = (Configuration) methodGetConfiguration.invoke(amn);

                // config.userSetLocale = true;
                Class configClass = config.getClass();
                Field f = configClass.getField("userSetLocale");
                f.setBoolean(config, true);

                // set the locale to the new value
                config.locale = locale;

                // amn.updateConfiguration(config);
                Method methodUpdateConfiguration = amnClass.getMethod("updateConfiguration", Configuration.class);
                methodUpdateConfiguration.setAccessible(true);
                methodUpdateConfiguration.invoke(amn, config);
            } catch (Exception e){
                Log.e(TAG, "Failed to set system locale. " + e.getMessage(), e);
            }
        }
    }
}
