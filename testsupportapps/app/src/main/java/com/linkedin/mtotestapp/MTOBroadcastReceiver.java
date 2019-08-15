package com.linkedin.mtotestapp;

import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.util.Log;


public class MTOBroadcastReceiver extends BroadcastReceiver {

    private static final String TAG = "MTO-TEST";

    final static String ACTION_TEST_ONLY_SEND_CMD = "com.linkedin.mto.FOR_TEST_ONLY_SEND_CMD";


    @Override
    public void onReceive(Context context, Intent intent) {
        /*
        A broadcast receiver that listens to ACTION_TEST_ONLY_SEND_CMD intent invoke call
        and log a test message for testing purpose.
         */
        if (intent.getAction() == null) {
            return;
        }
        if (intent.getAction().equals(ACTION_TEST_ONLY_SEND_CMD)) {
            // This is only for test purposes to send a command over to server
            // and allow server to test response logic
            Log.d(TAG, "Sending command \"" + intent.getStringExtra("command") + "\"");
        } else {
            Log.d(TAG, "Received an unknown action");
        }

    }
}
