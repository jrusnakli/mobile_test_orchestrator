import queue
import pickle
import subprocess
import os
import sys
import time
import configparser
from pathlib import Path
from apk_bitminer.parsing import DexParser
from multiprocessing.managers import BaseManager
from androidtestorchestrator import TestSuite

WORKER_SERVER1 = "hwei@52.247.198.26:~"
WORKER_SERVER2 = "userhelen@51.143.102.0:~"
PORT_NUM = 55000
AUTH_KEY = b'abcrde'


def make_server_manager(port, authkey):
    """
    Create a manager for server, listening on the given port.

    :param port: used for network traffic between VMs and Master to User.
    port enabled on azure vms (currently using 55000). For more details on how to configure
    inbound/outbound connections visit https://docs.microsoft.com/en-us/azure/virtual-network/security-overview
    :param authkey: a byte string which can be thought of as a password

    :return: a manager object with get_job_q,  get_result_q and get_token methods.
    """
    job_queue = queue.Queue()
    result_queue = queue.Queue()
    token = queue.Queue()

    class JobQueueManager(BaseManager):
        pass

    # get_{job|result}_q return synchronized proxies for the actual Queue objects.
    # register get_job_queue, get_result_queue and get_token methods to share the
    # job_queue, result_queue, and token between Master server and Worker servers
    # token used as a flag to indicate if tests are finished
    JobQueueManager.register('get_job_queue', callable=lambda: job_queue)
    JobQueueManager.register('get_result_queue', callable=lambda: result_queue)
    JobQueueManager.register('get_token', callable=lambda: token)

    master_manager = JobQueueManager(address=('', port), authkey=authkey)
    master_manager.start()
    print('Server started at port %s' % port)
    return master_manager


def extract_to_job_queue(apk_path):
    """
    Extract test suites from test apk and add test suites to job queue in Master server
    :param apk_path: test apk path
    """
    test_list = list(DexParser.parse(apk_path))
    for test in test_list:
        test_suites = TestSuite(name='test_suite', arguments=["-e", "class", test])
        shared_job_queue.put(pickle.dumps(test_suites))


if __name__ == "__main__":

    """
    To run this script standalone:  
    python masterserver.py <path of args.txt> 
    (without using agent scripts to run automatically -- details in Readme)

    args.txt example:
    [settings]
    app_apk_path = /Users/hewei/app-debug.apk
    test_apk_path = /Users/hewei/app-debug-androidTest.apk  

    The work_dir is "/mto_work_dir" under home_dir, agent scripts deployed to Azure vm 
    creates work_dir if not exists, to run script standalone, mto_work_dir need to exists.
    """

    # worker_server1 and worker_server2 are the worker VMs we used on Azure, # port enabled on azure vms
    # (currently using 55000).  # auth_key works as a password, send from user to VMs, currently hard corded
    # as a general key for testing purpose.
    worker_server1 = WORKER_SERVER1
    worker_server2 = WORKER_SERVER2
    port_num = PORT_NUM
    auth_key = AUTH_KEY

    manager = make_server_manager(port_num, auth_key)
    shared_job_queue = manager.get_job_queue()
    shared_result_queue = manager.get_result_queue()
    counter = manager.get_token()

    config = configparser.ConfigParser()
    work_dir = str(Path.home()) + "/mto_work_dir"

    while True:
        # Current POC design is using "scp " to send test artifacts from user end to Azure Master server.
        # To make sure all contents in zipfile are sent to vm before unzip, a dummy (flag) are followed
        # after zipfile. Master server checks "new_job_flag" as a flag of test artifacts are ready.
        # For future work, Https can be implemented here.
        new_job_flag = work_dir + "/new_job_flag"
        zip_file = work_dir + "/work_file.zip"

        if os.path.isfile(new_job_flag):
            config.read(sys.argv[1])
            test_apk_path = config.get("settings", "test_apk_path")
            extract_to_job_queue(test_apk_path)

            # scp message to worker server
            p1 = subprocess.Popen(["scp", zip_file, worker_server1])
            p2 = subprocess.Popen(["scp", zip_file, worker_server2])
            os.waitpid(p1.pid, 0)
            os.waitpid(p2.pid, 0)

            # wait until all results are fetched by user
            test_list = list(DexParser.parse(test_apk_path))
            while True:
                if counter.qsize() == len(test_list) and shared_result_queue.qsize() == 0:
                    break
                time.sleep(3)
            manager.shutdown()


