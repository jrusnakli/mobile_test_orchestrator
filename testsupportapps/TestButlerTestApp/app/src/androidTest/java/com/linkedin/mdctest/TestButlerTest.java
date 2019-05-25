package com.linkedin.mdctest;

import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Environment;
import android.provider.Settings;
import android.support.annotation.IntegerRes;
import android.support.test.InstrumentationRegistry;
import android.support.test.rule.ActivityTestRule;
import android.support.test.runner.AndroidJUnit4;
import android.util.Log;
import android.view.Surface;

import org.junit.Before;
import org.junit.BeforeClass;
import org.junit.FixMethodOrder;
import org.junit.Rule;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.MethodSorters;

import static org.junit.Assert.*;
import com.linkedin.android.testbutler.TestButler;

import java.io.File;
import java.util.Set;

import org.junit.AssumptionViolatedException;

/**
 * Instrumentation tests that provide a fixed test status for known output to cloud test runs.
 * Also, test of test butler code is provided here
 *
 * @see <a href="http://d.android.com/tools/testing">Testing documentation</a>
 */
@RunWith(AndroidJUnit4.class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
public class TestButlerTest {

    private static final String TAG = "TestButlerTest";

    @Rule
    public final ActivityTestRule<MainActivity> mRule = new ActivityTestRule<MainActivity>(MainActivity.class);

    @BeforeClass
    public static void setUp() {
        Log.d(TAG, "Setting up Test Butler");
        TestButler.setup(InstrumentationRegistry.getTargetContext());
    }

    /**
     * These tests will only pass when server-side script is active and working
     * This is in essence an integration test and is intended to be run as part
     * of the full sequence of running a cloud-based test
     * @throws Throwable
     */
    @Test
    public void testTestButlerRotation() throws Throwable {
        // Context of the app under test.
        Context appContext = InstrumentationRegistry.getTargetContext();
        TestButler.setRotation(Surface.ROTATION_0);
        String before = String.valueOf(Surface.ROTATION_0);
        assertEquals(before, String.valueOf(Surface.ROTATION_0));
        Log.d(TAG, "Reqeusting set roatation to 90...");
        TestButler.setRotation(Surface.ROTATION_90);
        String after = Settings.System.getString(appContext.getContentResolver(),
                Settings.System.USER_ROTATION);
        assertEquals(after, String.valueOf(Surface.ROTATION_90));
        // this is only for visual checkout that the rotation happened:
        Thread.sleep(500);
    }

    @Test()
    public void testTestButlerSetWifiState() throws Throwable {
        // Context of the app under test.
        Context appContext = InstrumentationRegistry.getTargetContext();
        TestButler.setWifiState(true);
        String now = Settings.System.getString(appContext.getContentResolver(),
                Settings.Global.WIFI_ON);
        assertEquals(now, "1");
        TestButler.setWifiState(false);
        String after = Settings.System.getString(appContext.getContentResolver(),
                Settings.Global.WIFI_ON);
        assertEquals(after, "0");
    }

    @Test
    public void testTestButlerSetImmersiveModeConfirmation() throws Throwable {
        // Context of the app under test.
        Context appContext = InstrumentationRegistry.getTargetContext();
        TestButler.setImmersiveModeConfirmation(true);
        // unclear if there is a way to test this value was changed, as it is not exposed in public API (?)
    }

    @Test
    public void testTestButlerSetLocationModeHigh() throws Throwable {
        // Context of the app under test.
        Context appContext = InstrumentationRegistry.getTargetContext();

        if(!appContext.getPackageManager().hasSystemFeature(PackageManager.FEATURE_LOCATION_NETWORK )||
           !appContext.getPackageManager().hasSystemFeature(PackageManager.FEATURE_WIFI) ||
           !appContext.getPackageManager().hasSystemFeature(PackageManager.FEATURE_LOCATION_GPS)){
            throw new AssumptionViolatedException("Location mode high accuracy not supported on this device");
        }
        {
            TestButler.setLocationMode(Settings.Secure.LOCATION_MODE_OFF);
            String after = String.valueOf(Settings.Secure.getInt(appContext.getContentResolver(),
                    Settings.Secure.LOCATION_MODE));
            assertEquals(after, Integer.toString(Settings.Secure.LOCATION_MODE_OFF));
        }
        {
            TestButler.setLocationMode(Settings.Secure.LOCATION_MODE_HIGH_ACCURACY);
            String after = String.valueOf(Settings.Secure.getInt(appContext.getContentResolver(),
                    Settings.Secure.LOCATION_MODE));
            assertEquals(after, Integer.toString(Settings.Secure.LOCATION_MODE_HIGH_ACCURACY));
        }
    }

    @Test
    public void testTestButlerSetLocationModeSensorsOnly() throws Throwable {
        // Context of the app under test.
        Context appContext = InstrumentationRegistry.getTargetContext();

        if(!appContext.getPackageManager().hasSystemFeature(PackageManager.FEATURE_WIFI) &&
           !appContext.getPackageManager().hasSystemFeature(PackageManager.FEATURE_LOCATION_GPS)){
            throw new AssumptionViolatedException("Location sensors only not supported on this device");
        }

        {
            TestButler.setLocationMode(Settings.Secure.LOCATION_MODE_OFF);
            String after = String.valueOf(Settings.Secure.getInt(appContext.getContentResolver(),
                    Settings.Secure.LOCATION_MODE));
            assertEquals(after, Integer.toString(Settings.Secure.LOCATION_MODE_OFF));
        }
        {
            TestButler.setLocationMode(Settings.Secure.LOCATION_MODE_SENSORS_ONLY);
            String after = String.valueOf(Settings.Secure.getInt(appContext.getContentResolver(),
                    Settings.Secure.LOCATION_MODE));
            assertEquals(after, Integer.toString(Settings.Secure.LOCATION_MODE_SENSORS_ONLY));
        }
    }

    @Test
    public void testTestButlerSetLocationModeBatterySaver() throws Throwable {
        // Context of the app under test.
        Context appContext = InstrumentationRegistry.getTargetContext();

        if(!appContext.getPackageManager().hasSystemFeature(PackageManager.FEATURE_WIFI)){
            // if no wifi location, Android will post "gps" as LOCATION_MODE_SENSORS_ONLY
            throw new AssumptionViolatedException("Location sensors only not supported on this device");
        }

        {
            TestButler.setLocationMode(Settings.Secure.LOCATION_MODE_OFF);
            String after = String.valueOf(Settings.Secure.getInt(appContext.getContentResolver(),
                    Settings.Secure.LOCATION_MODE));
            assertEquals(after, Integer.toString(Settings.Secure.LOCATION_MODE_OFF));
        }
        {
            TestButler.setLocationMode(Settings.Secure.LOCATION_MODE_BATTERY_SAVING);
            String after = String.valueOf(Settings.Secure.getInt(appContext.getContentResolver(),
                    Settings.Secure.LOCATION_MODE));
            assertEquals(after, Integer.toString(Settings.Secure.LOCATION_MODE_BATTERY_SAVING));
        }
    }

}
