package com.linkedin.mdctest;

import android.os.Bundle;
import android.support.test.runner.AndroidJUnitRunner;
import android.util.Log;
import com.linkedin.android.testbutler.TestButler;


public class TestButlerTestRunner extends AndroidJUnitRunner {

    private static final String TAG = "TestButlerTestRunner";

    @Override
    public void onStart() {
      Log.d(TAG, "Setting up Test Butler");
      TestButler.setup(getTargetContext());
      super.onStart();
    }

    @Override
    public void finish(int resultCode, Bundle results) {
      Log.d(TAG, "Teardown Test Butler");
      TestButler.teardown(getTargetContext());
      super.finish(resultCode, results);
    }
}
