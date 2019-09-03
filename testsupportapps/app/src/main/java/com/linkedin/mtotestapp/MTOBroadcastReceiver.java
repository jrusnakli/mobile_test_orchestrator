/*
BSD 2-CLAUSE LICENSE

Copyright 2019 LinkedIn Corporation
All Rights Reserved.
Redistribution and use in source and binary forms, with or
without modification, are permitted provided that the following
conditions are met:
1. Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above
copyright notice, this list of conditions and the following
disclaimer in the documentation and/or other materials provided
with the distribution.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

package com.linkedin.mtotestapp;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
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
