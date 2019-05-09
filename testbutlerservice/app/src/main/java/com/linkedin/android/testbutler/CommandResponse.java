package com.linkedin.android.testbutler;

class CommandResponse {
    private final int statusCode;
    private final String message;

    CommandResponse(final int code,
                           final String msg){
        statusCode = code;
        message = msg;
    }

    final int getStatusCode() {
        return statusCode;
    }

    final String getMessage(){
        return message;
    }

}
