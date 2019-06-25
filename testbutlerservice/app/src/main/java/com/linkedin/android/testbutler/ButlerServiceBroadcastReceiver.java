package com.linkedin.android.testbutler;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.util.Log;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.Locale;


public class ButlerServiceBroadcastReceiver extends BroadcastReceiver {
  /**
   * A broadcast receiver that listens to the ACTION_CMD_RESPONSE and ACTION_TEST_ONLY_SEND_CMD intent invoke call
   * and process the server side response. Currently server side invokes these intent by calling adb shell am boadcast command
   */

  //for debug:
  private static final String TAG = CommandInvocation.TAG;


  // These should match declaration of intents in Manifest.xml
  final static String ACTION_CMD_RESPONSE = "com.linkedin.android.testbutler.COMMAND_RESPONSE";
  final static String ACTION_TEST_ONLY_SEND_CMD = "com.linkedin.android.testbutler.FOR_TEST_ONLY_SEND_CMD";
  final static String ACTION_SET_SYSTEM_LOCALE = "com.linkedin.android.testbutler.SET_SYSTEM_LOCALE";

  @Override
  public void onReceive(Context context, Intent intent) {
    Log.d(TAG, "Received context action: " + intent.getAction());
    onHandleIntent(intent);

  }

  protected void onHandleIntent(Intent intent) {
    /**
     * A test-only intent ACTION_TEST_ONLY_SEND_CMD is provided for use in testing
     * this TestButler code (only to be used for that purpose).  This provides
     * an easy mechanism to test the very fundamentals of interaction between
     * TestButler running on a device and the server to which the device is connected.
     * It eliminates the complexity of needing a third component (an explicit test app)
     * that could make debugging the basics far more difficult.
     */
    if (intent.getAction() == null) {
      return;
    }
    if (intent.getAction().equals(ACTION_CMD_RESPONSE)) {
      final String response = intent.getStringExtra("response");
      try {
        CommandInvocation.processServerResponse(response);
      } catch (Exception e) {
        CommandInvocation.signalError("ERROR: exception encountered while handling command response " + e.getMessage());
      }
    } else if (intent.getAction().equals(ACTION_TEST_ONLY_SEND_CMD)) {
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
      } catch (Exception e) {
        Log.e(TAG, "Failed to set system locale. " + e.getMessage(), e);
      }
    }
  }
}
