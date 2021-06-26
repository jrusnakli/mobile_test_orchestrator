from multiprocessing.managers import BaseManager
from apk_bitminer.parsing import DexParser
import configparser
import sys
import time

MASTER_IP = "52.247.223.117"
PORT_NUM = 55000
AUTH_KEY = b'abcrde'


def make_user_manager(ip, port, authkey):
    """
    Create a manager for user end, listening on the given port.

    :param port: used for network traffic between VMs and Master to User.
    port enabled on azure vms (currently using 55000). For more details on how to configure
    inbound/outbound connections visit https://docs.microsoft.com/en-us/azure/virtual-network/security-overview
    :param authkey: a byte string which can be thought of as a password

    :return: a manager object with get_job_q,  get_result_q and get_token methods.
    """

    class ServerQueueManager(BaseManager):
        pass

    # get_{result}_q return synchronized proxies for the actual Queue object.
    ServerQueueManager.register('get_result_queue')
    manager = ServerQueueManager(address=(ip, port), authkey=authkey)
    manager.connect()
    print('user connected to master %s:%s' % (ip, port))
    return manager


if __name__ == "__main__":
    """
    To run user.py script: 
    python user.py <path of args.txt>

    args.txt example:
    [settings]
    app_apk_path = /Users/hewei/app-debug.apk
    test_apk_path = /Users/hewei/app-debug-androidTest.apk  

    If masterserver.py deployed locally (not on Azure vm1 - Master vm) for local test, change 
    MASTER_IP = "52.247.223.117" to MASTER_IP = "localhost"
    """

    # port enabled on azure vms (currently using 55000). # auth_key works as a password, send from user to VMs,
    # currently hard corded as a general key for testing purpose.
    port_num = PORT_NUM
    auth_key = AUTH_KEY
    master_server = MASTER_IP

    manager = make_user_manager(master_server, port_num, auth_key)
    result_queue = manager.get_result_queue()

    config = configparser.ConfigParser()
    config.read(sys.argv[1])
    test_apk_path = (config.get("settings", "test_apk_path"))

    test_lists = list(DexParser.parse(test_apk_path))
    token = len(test_lists)
    while token != 0:
        if result_queue.qsize() != 0:
            result = result_queue.get()
            print(result)
            token -= 1
        else:
            time.sleep(2)


