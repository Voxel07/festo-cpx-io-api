"""Example code for CPX-AP digital output"""

# import the library
from cpx_io.cpx_system.cpx_ap.cpx_ap import CpxAp
import time

NUMBER_OF_CHANNELS = 8
TIME_ON = 5
TIME_OFF = 2

setLow = [0] * NUMBER_OF_CHANNELS
setHigh = [1] * NUMBER_OF_CHANNELS

with CpxAp(ip_address="192.168.1.11", timeout=0) as myCPX:

    for module in myCPX.modules:
        print(f"Module name: {module.name}")
        print(f"Module pos: {module.position}")
        print("-" * 30)

        if module.name.startswith("vabx"):
            module.write_channels(setHigh)  # Ensure all channels start LOW

        time.sleep(1)  # Short delay to ensure commands are processed

    # Access individual modules by index
    # print(f"Module name: {myCPX.modules[6].name}")
    # current_time = time.strftime("%H:%M:%S", time.localtime())
    # print(f"Setting all outputs HIGH for {TIME_ON} seconds start {current_time}")
    # VABX.write_channels(setHigh)

    # while True:

    # Test 1: Toggle all outputs HIGH and LOW
    # current_time = time.strftime("%H:%M:%S", time.localtime())
    # print(f"Setting all outputs HIGH for {TIME_ON} seconds start {current_time}")
    # VABX.write_channels(setHigh)
    # time.sleep(2)

    # current_time = time.strftime("%H:%M:%S", time.localtime())
    # print(f"Setting all outputs LOW for {TIME_OFF} seconds start {current_time}")
    # VABX.write_channels(setLow)

    # time.sleep(1)

    # Test 2: Toggle 4 valves at a time
    # print("Starting 4-valve test cycle")
    # for group in range(0, NUMBER_OF_CHANNELS, 4):
    #     test_pattern = [0] * NUMBER_OF_CHANNELS
    #     # Turn on 4 valves
    #     for i in range(4):
    #         if group + i < NUMBER_OF_CHANNELS:
    #             test_pattern[group + i] = 1

    #     current_time = time.strftime("%H:%M:%S", time.localtime())
    #     print(
    #         f"Turning ON valves {group + 1} to {min(group + 4, NUMBER_OF_CHANNELS)} at {current_time}"
    #     )
    #     VABX.write_channels(test_pattern)
    #     VABX2.write_channels(test_pattern)
    #     time.sleep(1)

    #     # Turn them off
    #     print(
    #         f"Turning OFF valves {group + 1} to {min(group + 4, NUMBER_OF_CHANNELS)}"
    #     )
    #     VABX.write_channels(setLow)
    #     VABX2.write_channels(setLow)
    #     time.sleep(1)

    # Test 3 inc by 1 until all are on
    # active_channels = 0
    # for channel in range(NUMBER_OF_CHANNELS):
    #     current_time = time.strftime("%H:%M:%S", time.localtime())
    #     print(f"Setting channel {channel +1} HIGH ")
    #     active_channels |= 1 << channel
    #     VABX.write_channels([int(b) for b in format(active_channels, "032b")[::-1]])
    #     time.sleep(1)

    # # Keep all channels active for 10 seconds
    # print("All channels active for 10 seconds")
    # time.sleep(10)

    # # Turn all channels off
    # print("Turning all channels off")
    # VABX.write_channels(setLow)
    # time.sleep(1)

    # Test 4 lauflicht (with configurable end channel)
    # SLEEP_BETWEEN_CHANNELS = 0.1
    # RUNNING_LIGHT_END_CHANNEL = 32  # 1-based, max is NUMBER_OF_CHANNELS (48)

    # end_channel = max(1, min(RUNNING_LIGHT_END_CHANNEL, NUMBER_OF_CHANNELS))
    # end_index = end_channel - 1

    # active_channels = [0] * NUMBER_OF_CHANNELS

    # # Forward pass (0 to end_index)
    # for channel in range(0, end_index + 1):
    #     print(f"Setting channel {channel + 1} HIGH")
    #     if channel > 0:
    #         active_channels[channel - 1] = 0
    #         active_channels[channel] = 1
    #         VABX.write_channels(active_channels)
    #         time.sleep(SLEEP_BETWEEN_CHANNELS)

    # # Backward pass (end_index down to 0)
    # for channel in range(end_index, -1, -1):
    #     print(f"Setting channel {channel + 1} HIGH")
    #     if channel < end_index:
    #         active_channels[channel + 1] = 0
    #         active_channels[channel] = 1
    #         VABX.write_channels(active_channels)
    #         time.sleep(SLEEP_BETWEEN_CHANNELS)
