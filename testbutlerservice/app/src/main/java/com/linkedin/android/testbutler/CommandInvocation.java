package com.linkedin.android.testbutler;

import android.util.Log;

import java.util.concurrent.ConcurrentHashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

class CommandInvocation {

    //for debug:
    static final String TAG = "TestButler";

    private static final Map<Integer, CompletableFuture<CommandResponse> > cmdResponses= new ConcurrentHashMap();
    private static int counter = 0;
    private static final ExecutorService executor = Executors.newSingleThreadExecutor();

    /**
     * Tell server an error occurred (signalling server should handle this error)
     * @param message message to server
     */
    static void signalError(final String message){
        Log.e(TAG, message);
    }

    static synchronized int incrementCounter(){
        counter += 1;
        return counter;
    }

    static Future<CommandResponse> invoke(final String cmd) {
        final int count  = incrementCounter();
        final String put_cmd = String.valueOf(count) + " " + cmd;

        // this message signals server to invoke adb command to set property:
        Log.i(TAG, put_cmd);
        CompletableFuture<CommandResponse> futureResponse = new CompletableFuture<>();
        synchronized(cmdResponses){
            cmdResponses.put(count, futureResponse);
        }
        return futureResponse;

    }

    static void processServerResponse(final String response) throws Exception{
        //Log.d(TAG, "GOT RESPONSE " + response);
        final String[] elements = response.split(",", 3);
        // if not a valid lookup id as first element, throw Exception right away
        try{
            Integer.valueOf(elements[0]);
        } catch (Exception e) {
            throw new Exception("Invalid response from server; first two element should be ints: " +
                    response);
        }
        final int lookupId = Integer.valueOf(elements[0]);
        synchronized(cmdResponses){
            final CompletableFuture<CommandResponse> pendingResponse = cmdResponses.get(lookupId);
            if (pendingResponse != null){
                cmdResponses.remove(lookupId);
            }
            try {
                final int statusCode = Integer.valueOf(elements[1]);
                final String message = elements[2];
                if (pendingResponse == null) {
                    signalError("Error in processing command response: Unknown lookupId " +
                            String.valueOf(lookupId) + ". Keys are " + cmdResponses.keySet());
                } else {
                    // set the response and then release semaphore
                    CommandResponse cmdResponse = new CommandResponse(statusCode, message);
                    Log.d(TAG, "CMD RESPONSE MSG: " + message);
                    Log.d(TAG, "CMD RESPONSE STATUS: " + statusCode);

                    pendingResponse.complete(cmdResponse);
                }
            } catch (Exception e) {
                signalError("Exception processing command response: " + e.getMessage());
                if(pendingResponse != null) {
                    pendingResponse.cancel(true);
                }
        }
        }
    }
}
