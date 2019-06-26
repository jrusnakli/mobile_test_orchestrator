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

import android.app.Notification;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.IBinder;
import android.os.RemoteException;
import android.provider.Settings;
import android.support.annotation.Nullable;
import android.support.v4.app.JobIntentService;
import android.support.v4.app.NotificationCompat;
import android.util.Log;
import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.text.MessageFormat;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;


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
    //for debug:
    private static final String TAG = CommandInvocation.TAG;

    private static final Gson GSON = new Gson();

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
        private String getStringProperty(final String namespace, final String key)
            throws Settings.SettingNotFoundException {
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
        private Integer getIntProperty(final String namespace, final String key)
            throws Settings.SettingNotFoundException {
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
        private CommandResponse invoke(final String cmd)
            throws InterruptedException, ExecutionException, TimeoutException {
            Future<CommandResponse> futureResponse = CommandInvocation.invoke(cmd);

            CommandResponse response = futureResponse.get(1, TimeUnit.SECONDS);
            if (response.getStatusCode() != 0) {
                // log debug message, as server should handle as error/exception
                Log.d(TAG, "Server-side error: " + response.getMessage());
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
        private CommandResponse sendSetStringProperty(final String namespace, final String key, String value) {
            try {
                if (getStringProperty(namespace, key).toLowerCase().equals(value.toLowerCase())) {
                    return new CommandResponse(0,
                        "System string property " + namespace + ": " + key + " is already set to " + value);
                }
                final String put_cmd = SETTING_PREFIX + " " + namespace + " " + key + " " + value + "\n";
                return invoke(put_cmd);
            } catch (InterruptedException | TimeoutException | ExecutionException e) {
                CommandInvocation.signalError(
                    "ERROR: Set of new system property " + namespace + ":" + key + " failed: Interrupted: "
                        + e.getMessage());
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
        private CommandResponse sendSetIntProperty(final String namespace, final String key, int value) {
            try {
                if (getIntProperty(namespace, key).equals(value)) {
                    return new CommandResponse(0,
                        "System integer property " + namespace + ": " + key + " is already set to " + value);
                }
                final String put_cmd = SETTING_PREFIX + " " + namespace + " " + key + " " + value + "\n";
                return invoke(put_cmd);
            } catch (InterruptedException | TimeoutException | ExecutionException e) {
                CommandInvocation.signalError(
                    "ERROR: Set of new system property " + namespace + ":" + key + " failed: Interrupted: "
                        + e.toString() + ":" + e.getMessage());
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

        private void onError(String namespace, String key, String expectedValue, CommandResponse response) {
            CommandInvocation.signalError(
                "ERROR: Set of new system property " + namespace + ":" + key + " to " + expectedValue + " failed");
            try {
                Log.d(TAG, "Value is now at: " + getStringProperty(namespace, key) + " but looking for " + expectedValue
                    .toLowerCase());
            } catch (Settings.SettingNotFoundException e) {
                // not log message to present
            }
            if (response != null) {
                Log.e(TAG, MessageFormat.format("Message from host: {0}", response.getMessage()));
            }
        }

        private void onErrorInt(String namespace, String key, int expectedValue, CommandResponse response) {
            CommandInvocation.signalError(
                "ERROR: Set of new system property " + namespace + ":" + key + " to " + String.valueOf(expectedValue)
                    + " failed");
            try {
                Log.d(TAG, "Value is now at: " + String.valueOf(getIntProperty(namespace, key)) + " but looking for "
                    + String.valueOf(expectedValue));
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
        public boolean setWifiState(boolean enabled) {
            CommandResponse response = sendSetIntProperty(NS_GLOBAL, "wifi_on", enabled ? 1 : 0);
            if (response == null || response.getStatusCode() != 0) {
                onError(NS_GLOBAL, "wifi_on", enabled ? "1" : "0", response);
            }
            return validateIntProperty(NS_GLOBAL, "wifi_on", enabled ? 1 : 0);
        }

        @Override
        public boolean setLocationMode(int locationMode) throws RemoteException {
            if (Settings.Secure.LOCATION_MODE_HIGH_ACCURACY == locationMode) {
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+gps");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+network");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+wifi");
            } else if (Settings.Secure.LOCATION_MODE_SENSORS_ONLY == locationMode) {
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "+gps");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "-network");
                sendSetStringProperty(NS_SECURE, "location_providers_allowed", "-wifi");
            } else if (Settings.Secure.LOCATION_MODE_BATTERY_SAVING == locationMode) {
                if (!getPackageManager().hasSystemFeature(PackageManager.FEATURE_LOCATION)) {
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
            if (response == null || response.getStatusCode() != 0) {
                onErrorInt(NS_SYSTEM, "accelerometer_rotation", 0, response);
            }
            response = sendSetIntProperty(NS_SYSTEM, "user_rotation", rotation);
            if (response == null || response.getStatusCode() != 0) {
                onErrorInt(NS_SYSTEM, "user_rotation", rotation, response);
            }
            return validateIntProperty(NS_SYSTEM, "accelerometer_rotation", 0) && validateIntProperty(NS_SYSTEM,
                "user_rotation", rotation);
        }

        @Override
        public boolean setGsmState(boolean enabled) {
            return false;
        }

        @Override
        public boolean grantPermission(String packageName, String permission) {
            int hasPerm = getApplicationContext().getPackageManager().checkPermission(permission, packageName);
            if (hasPerm == PackageManager.PERMISSION_GRANTED) {
                Log.i(TAG, "Already granted: permission " + permission + " to pacakge " + packageName);
                return true;
            }
            JsonObject json = new JsonObject();
            json.addProperty("type", "permission");
            json.addProperty("package", packageName);
            JsonArray permissionsJsonArray = new JsonArray();
            permissionsJsonArray.add(permission);
            json.add("permissions", permissionsJsonArray);
            String jsonString = GSON.toJson(json);

            final String cmd = GRANT_PREFIX + jsonString;
            try {
                invoke(cmd);
            } catch (InterruptedException | ExecutionException | TimeoutException e) {
                CommandInvocation.signalError("ERROR: Execution error granting permission: " + e.getMessage());
                return false;
            }
            return getApplicationContext().getPackageManager().checkPermission(permission, packageName)
                == PackageManager.PERMISSION_GRANTED;
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
            CommandResponse response =
                sendSetStringProperty(NS_SECURE, "immersive_mode_confirmation", enabled ? "confirmed" : "\"\"\"\"");
            if (response == null || response.getStatusCode() != 0) {
                onError(NS_SECURE, "immersive_mode_confirmation", enabled ? "confirmed" : "", response);
            }
            return validateStringProperty(NS_SECURE, "immersive_mode_confirmation", enabled ? "confirmed" : "");
        }
    };

    public ButlerService() {
        super();
    }

    @Override
    public void onCreate() {
        Notification notification =
            new NotificationCompat.Builder(this, "test_butler_channel_id_01").setContentTitle("")
                .setContentText("")
                .build();
        this.startForeground(0, notification);
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
        Log.d(TAG, "OnstartCommand is called with " + "Action: " + intent.getAction());
        Notification notification =
            new NotificationCompat.Builder(this, "test_butler_channel_id_01").setContentTitle("")
                .setContentText("")
                .build();
        this.startForeground(0, notification);
        super.onStartCommand(intent, flags, startId);
        return android.app.Service.START_STICKY;
    }

    @Nullable
    @Override
    protected void onHandleWork(Intent intent) {
    }
}