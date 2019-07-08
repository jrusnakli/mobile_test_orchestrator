package com.linkedin.mdctest;

import android.provider.Settings;
import android.content.Context;
import android.support.test.InstrumentationRegistry;
import android.support.test.runner.AndroidJUnit4;
import android.util.Log;
import android.view.Surface;

import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.MethodSorters;
import org.junit.BeforeClass;

import static org.junit.Assert.*;

import com.linkedin.android.testbutler.TestButler;

@RunWith(AndroidJUnit4.class)
public class TestButlerStressTest{
    private static final String TAG = "TestButlerStressTest";

    @BeforeClass
    public static void setUp() {
        Log.d(TAG, "Setting up Test Butler");
        TestButler.setup(InstrumentationRegistry.getTargetContext());
    }

    /**
     *  This tests ability to send many commands and receive from the host in short order
     **/
    @Test
    public void testTestButlerStress() throws Throwable {
        // Context of the app under test.
        Context appContext = InstrumentationRegistry.getTargetContext();
        for (int i = 0; i < 100; ++i){
            TestButler.setRotation(Surface.ROTATION_0);
            String before = String.valueOf(Surface.ROTATION_0);
            assertEquals(before, String.valueOf(Surface.ROTATION_0));
            Log.d(TAG, "Requesting set rotation to 90...");
            TestButler.setRotation(Surface.ROTATION_90);
            String after = Settings.System.getString(appContext.getContentResolver(),
                    Settings.System.USER_ROTATION);
            assertEquals(after, String.valueOf(Surface.ROTATION_90));
        }
    }
}