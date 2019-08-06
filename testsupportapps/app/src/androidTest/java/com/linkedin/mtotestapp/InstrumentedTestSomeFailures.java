package com.linkedin.mtotestapp;

import android.support.test.runner.AndroidJUnit4;

import org.junit.Test;
import org.junit.runner.RunWith;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

@RunWith(AndroidJUnit4.class)
public class InstrumentedTestSomeFailures {
    @Test
    public void testFail() throws Exception {
        fail();
    }

    @Test
    public void testSuccess() throws Exception {
        assertEquals(1, 1);
    }

}
