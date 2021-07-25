import asyncio
import re
import subprocess
import time
from typing import Optional

from mobiletestorchestrator.device import Device, RemoteDeviceBased


class DeviceInteraction(RemoteDeviceBased):
    """
    Provides API for equivalent of user-navigation along with related device queries
    """

    def go_home(self) -> None:
        """
        Equivalent to hitting home button to go to home screen
        """
        self.input("KEYCODE_HOME")

    # noinspection SpellCheckingInspection
    def home_screen_active(self) -> bool:
        """
        :return: True if the home screen is currently in the foreground. Note that system pop-ups will result in this
        function returning False.
        :raises Exception: if unable to make determination
        """
        found_potential_stack_match = False
        completed = self._device.execute_remote_cmd("shell", "dumpsys", "activity", "activities",
                                                    timeout=Device.TIMEOUT_ADB_CMD, stdout=subprocess.PIPE)
        # Find lines that look like this:
        #   Stack #0:
        # or
        #   Stack #0: type=home mode=fullscreen
        app_stack_pattern = re.compile(r'^Stack #(\d*):')
        stdout_lines = completed.stdout.splitlines()
        for line in stdout_lines:
            matches = app_stack_pattern.match(line.strip())
            if matches:
                if matches.group(1) == "0":
                    return True
                else:
                    found_potential_stack_match = True
                    break

        # Went through entire activities stack, but no line matched expected format for displaying activity
        if not found_potential_stack_match:
            raise Exception(
                f"Could not determine if home screen is in foreground because no lines matched expected "
                f"format of \"dumpsys activity activities\" pattern. Please check that the format did not change:\n"
                f"{stdout_lines}")

        # Format of activities was fine, but detected home screen was not in foreground. But it is possible this is a
        # Samsung device with silent packages in foreground. Need to check if that's the case, and app after them
        # is the launcher/home screen.
        foreground_activity = self._device.foreground_activity(ignore_silent_apps=True)
        return bool(foreground_activity and foreground_activity.lower() == "com.sec.android.app.launcher")

    def input(self, subject: str, source: Optional[str] = None) -> None:
        # noinspection SpellCheckingInspection
        """
                Send event subject through given source

                :param subject: event to send
                :param source: source of event, or None to default to "keyevent"
                """
        # noinspection SpellCheckingInspection
        self.device.execute_remote_cmd("shell", "input", source or "keyevent", subject, timeout=Device.TIMEOUT_ADB_CMD)

    def is_screen_on(self) -> bool:
        """
        :return: whether device's screen is on
        """
        completed = self.device.execute_remote_cmd("shell", "dumpsys", "activity", "activities",
                                                   stdout=subprocess.PIPE,
                                                   timeout=Device.TIMEOUT_ADB_CMD)
        lines = completed.stdout.splitlines()
        for msg in lines:
            if 'mInteractive=false' in msg or 'mScreenOn=false' in msg or 'isSleeping=true' in msg:
                return False
        return True

    def return_home(self, keycode_back_limit: int = 10) -> None:
        """
        Return to home screen as though the user did so via one or many taps on the back button.
        In this scenario, subsequent launches of the app will need to recreate the app view, but may
        be able to take advantage of some saved state, and is considered a warm app launch.

        NOTE: This function assumes the app is currently in the foreground. If not, it may still return to the home
        screen, but the process of closing activities on the back stack will not occur.

        :param keycode_back_limit: The maximum number of times to press the back button to attempt to get back to
           the home screen
        """
        back_button_attempt = 0

        while back_button_attempt <= keycode_back_limit:
            back_button_attempt += 1
            self.input("KEYCODE_BACK")
            if self.home_screen_active():
                return
            # Sleep for a second to allow for complete activity destruction.
            # TODO: ouch!! almost a 10 second overhead if we reach limit
            time.sleep(1)

        foreground_activity = self._device.foreground_activity(ignore_silent_apps=True)

        raise Exception(f"Max number of back button presses ({keycode_back_limit}) to get to Home screen has "
                        f"been reached. Found foreground activity {foreground_activity}. App closure failed.")

    def toggle_screen_on(self) -> None:
        """
        Toggle device's screen on/off
        """
        # noinspection SpellCheckingInspection
        self._device.execute_remote_cmd("shell", "input", "keyevent", "KEYCODE_POWER", timeout=10)


class AsyncDeviceInteraction(RemoteDeviceBased):
    """
    Provides API for equivalent of user-navigation along with related device queries
    """

    async def go_home(self) -> None:
        """
        Equivalent to hitting home button to go to home screen
        """
        await self.input("KEYCODE_HOME")

    async def home_screen_active(self) -> bool:
        """
        :return: True if the home screen is currently in the foreground. Note that system pop-ups will result in this
        function returning False.
        :raises Exception: if unable to make determination
        """
        found_potential_stack_match = False
        stdout_lines = []
        async with self.device.monitor_remote_cmd("shell", "dumpsys", "activity", "activities") as proc:
            # noinspection SpellCheckingInspection
            async for line in proc.output(unresponsive_timeout=self.device.TIMEOUT_ADB_CMD):
                stdout_lines.append(line)
                # Find lines that look like this:
                #   Stack #0:
                # or
                #   Stack #0: type=home mode=fullscreen
                app_stack_pattern = re.compile(r'^Stack #(\d*):')
                matches = app_stack_pattern.match(line.strip())
                if matches:
                    if matches.group(1) == "0":
                        return True
                    else:
                        found_potential_stack_match = True
                        break

        # Went through entire activities stack, but no line matched expected format for displaying activity
        if not found_potential_stack_match:
            raise Exception(
                f"Could not determine if home screen is in foreground because no lines matched expected "
                f"format of \"dumpsys activity activities\" pattern. Please check that the format did not change:\n"
                f"{stdout_lines}")

        # Format of activities was fine, but detected home screen was not in foreground. But it is possible this is a
        # Samsung device with silent packages in foreground. Need to check if that's the case, and app after them
        # is the launcher/home screen.
        foreground_activity = self._device.foreground_activity(ignore_silent_apps=True)
        return bool(foreground_activity and foreground_activity.lower() == "com.sec.android.app.launcher")

    async def input(self, subject: str, source: Optional[str] = None) -> None:
        # noinspection SpellCheckingInspection
        """
        Send event subject through given source

        :param subject: event to send
        :param source: source of event, or None to default to "keyevent"
        """
        # noinspection SpellCheckingInspection
        await self.device.execute_remote_cmd_async(
            "shell", "input", source or "keyevent", subject, timeout=Device.TIMEOUT_ADB_CMD)

    async def is_screen_on(self) -> bool:
        """
        :return: whether device's screen is on
        """
        async with self.device.monitor_remote_cmd("shell", "dumpsys", "activity", "activities") as proc:
            async for msg in proc.output(unresponsive_timeout=Device.TIMEOUT_ADB_CMD):
                if 'mInteractive=false' in msg or 'mScreenOn=false' in msg or 'isSleeping=true' in msg:
                    return False
        return True

    async def return_home(self, keycode_back_limit: int = 10, time_interval: float = 1.0) -> None:
        """
        Return to home screen as though the user did so via one or many taps on the back button.
        In this scenario, subsequent launches of the app will need to recreate the app view, but may
        be able to take advantage of some saved state, and is considered a warm app launch.

        NOTE: This function assumes the app is currently in the foreground. If not, it may still return to the home
        screen, but the process of closing activities on the back stack will not occur.

        :param keycode_back_limit: The maximum number of times to press the back button to attempt to get back to
           the home screen
        :param time_interval: time interval between input ("taps") calls
        """
        back_button_attempt = 0

        while back_button_attempt <= keycode_back_limit:
            back_button_attempt += 1
            await self.input("KEYCODE_BACK")
            if await self.home_screen_active():
                return
            # Sleep for a second to allow for complete activity destruction.
            await asyncio.sleep(time_interval)

        foreground_activity = self.device.foreground_activity(ignore_silent_apps=True)

        raise Exception(f"Max number of back button presses ({keycode_back_limit}) to get to Home screen has "
                        f"been reached. Found foreground activity {foreground_activity}. App closure failed.")

    async def toggle_screen_on(self) -> None:
        """
        Toggle device's screen on/off
        """
        # noinspection SpellCheckingInspection
        await self.device.execute_remote_cmd_async("shell", "input", "keyevent", "KEYCODE_POWER", timeout=10)
