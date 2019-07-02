"""
Pod related functionalities and context info

Each pod in the openshift cluster will have a corresponding pod object
"""
import logging
import re
import tempfile
from time import sleep
from threading import Thread
import base64

from ocs_ci.ocs.ocp import OCP
from ocs_ci.ocs import constants, defaults, workload
from ocs_ci.framework import config
from ocs_ci.ocs.exceptions import CommandFailed
from ocs_ci.ocs.resources.ocs import OCS
from ocs_ci.utility import templating

logger = logging.getLogger(__name__)


TEXT_CONTENT = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, "
    "sed do eiusmod tempor incididunt ut labore et dolore magna "
    "aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
    "ullamco laboris nisi ut aliquip ex ea commodo consequat. "
    "Duis aute irure dolor in reprehenderit in voluptate velit "
    "esse cillum dolore eu fugiat nulla pariatur. Excepteur sint "
    "occaecat cupidatat non proident, sunt in culpa qui officia "
    "deserunt mollit anim id est laborum."
)
TEST_FILE = '/var/lib/www/html/test'


class Pod(OCS):
    """
    Handles per pod related context
    """

    def __init__(self, **kwargs):
        """
        Initializer function

        kwargs:
            Copy of ocs/defaults.py::<some pod> dictionary
        """
        self.pod_data = kwargs
        super(Pod, self).__init__(**kwargs)

        self.temp_yaml = tempfile.NamedTemporaryFile(
            mode='w+', prefix='POD_', delete=False
        )
        self._name = self.pod_data.get('metadata').get('name')
        self._labels = self.get_labels()
        self._roles = []
        self.ocp = OCP(
            api_version=defaults.API_VERSION, kind=constants.POD,
            namespace=self.namespace
        )
        self.thread = None
        # TODO: get backend config !!

    @property
    def name(self):
        return self._name

    @property
    def namespace(self):
        return self._namespace

    @property
    def roles(self):
        return self._roles

    @property
    def labels(self):
        return self._labels

    def __setattr__(self, key, val):
        self.__dict__[key] = val

    def add_role(self, role):
        """
        Adds a new role for this pod

        Args:
            role (str): New role to be assigned for this pod
        """
        self._roles.append(role)

    def exec_cmd_on_pod(self, command, out_yaml_format=True):
        """
        Execute a command on a pod (e.g. oc rsh)

        Args:
            command (str): The command to execute on the given pod
            out_yaml_format (bool): whether to return yaml loaded python
                object OR to return raw output

        Returns:
            Munch Obj: This object represents a returned yaml file
        """
        rsh_cmd = f"rsh {self.name} "
        rsh_cmd += command
        return self.ocp.exec_oc_cmd(rsh_cmd, out_yaml_format)

    def get_labels(self):
        """
        Get labels from pod

        Raises:
            NotFoundError: If resource not found

        Returns:
            dict: All the openshift labels on a given pod
        """
        return self.pod_data.get('metadata').get('labels')

    def exec_ceph_cmd(self, ceph_cmd, format='json-pretty'):
        """
        Execute a Ceph command on the Ceph tools pod

        Args:
            ceph_cmd (str): The Ceph command to execute on the Ceph tools pod
            format (str): The returning output format of the Ceph command

        Returns:
            dict: Ceph command output

        Raises:
            CommandFailed: In case the pod is not a toolbox pod
        """
        if 'rook-ceph-tools' not in self.labels.values():
            raise CommandFailed(
                "Ceph commands can be executed only on toolbox pod"
            )
        ceph_cmd = ceph_cmd
        if format:
            ceph_cmd += f" --format {format}"
        out = self.exec_cmd_on_pod(ceph_cmd)

        # For some commands, like "ceph fs ls", the returned output is a list
        if isinstance(out, list):
            return [item for item in out if item]
        return out

    def run_io(
        self, storage_type, size, direction='rw', workers=1, runtime=60,
        run_in_bg=True
    ):
        """
        Execute FIO on a pod

        Args:
             storage_type (str): 'fs' or 'block'
             size (str): Size in MB, e.g. '200M'
             direction (str): Determines the operation:
                'ro', 'wo', 'rw' (default: 'rw')
            workers (int): Number of threads to execute FIO
            runtime (int): Number of seconds IO should run for
            run_in_bg (bool): True in case the FIO should run in background,
                False otherwise
        """
        def execute_fio():
            name = 'test_workload'
            spec = self.pod_data.get('spec')
            path = (
                spec.get('containers')[0].get('volumeMounts')[0].get(
                    'mountPath'
                )
            )
            work_load = 'fio'
            # few io parameters for Fio

            wl = workload.WorkLoad(
                name, path, work_load, storage_type, self, workers
            )
            assert wl.setup()
            if direction == 'rw':
                io_params = templating.load_yaml_to_dict(
                    constants.FIO_IO_RW_PARAMS_YAML
                )
            else:
                io_params = templating.load_yaml_to_dict(
                    constants.FIO_IO_PARAMS_YAML
                )
            io_params['runtime'] = runtime
            io_params['size'] = size

            future_result = wl.run(**io_params)
            logger.info(future_result.result())

        if run_in_bg:
            self.thread = Thread(target=execute_fio,)
            self.thread.start()
        else:
            execute_fio()


# Helper functions for Pods

def get_all_pods(namespace=None):
    """
    Get all pods in a namespace.
    If namespace is None - get all pods

    Returns:
        list: List of Pod objects
    """
    ocp_pod_obj = OCP(kind=constants.POD, namespace=namespace)
    pods = ocp_pod_obj.get()['items']
    pod_objs = [Pod(**pod) for pod in pods]
    return pod_objs


def get_ceph_tools_pod():
    """
    Get the Ceph tools pod

    Returns:
        Pod object: The Ceph tools pod object
    """
    ocp_pod_obj = OCP(
        kind=constants.POD, namespace=config.ENV_DATA['cluster_namespace']
    )
    ct_pod = ocp_pod_obj.get(
        selector='app=rook-ceph-tools'
    )['items'][0]
    assert ct_pod, f"No Ceph tools pod found"
    ceph_pod = Pod(**ct_pod)
    return ceph_pod


def check_file_existence(pod_obj, file_name):
    """
    Check if file exists inside the pod

    Args:
        pod_obj (Pod): The object of the pod
        file_name (str): The name (full path) of the file to look for inside
            the pod

    Returns:
        bool: True if the file exist, False otherwise
    """
    ret = pod_obj.exec_cmd_on_pod(f"bash -c \"find {file_name}\"")
    if re.search(file_name, ret):
        return True
    return False


def run_io_in_bg(pod_obj, expect_to_fail=False):
    """
    Run I/O in the background

    Args:
        pod_obj (Pod): The object of the pod
        expect_to_fail (bool): True for the command to be expected to fail
            (disruptive operations), False otherwise

    Returns:
        Thread: A thread of the I/O execution
    """
    logger.info(f"Running I/O on pod {pod_obj.name}")

    def exec_run_io_cmd(pod_obj, expect_to_fail):
        """
        Execute I/O
        """
        try:
            # Writing content to a new file every 0.01 seconds.
            # Without sleep, the device will run out of space very quickly -
            # 5-10 seconds for a 5GB device
            pod_obj.exec_cmd_on_pod(
                f"bash -c \"let i=0; while true; do echo {TEXT_CONTENT} "
                f">> {TEST_FILE}$i; let i++; sleep 0.01; done\""
            )
        # Once the pod gets deleted, the I/O execution will get terminated.
        # Hence, catching this exception
        except CommandFailed as ex:
            if expect_to_fail:
                if re.search("code 137", str(ex)):
                    logger.info("I/O command got terminated as expected")
                    return
            raise ex

    thread = Thread(target=exec_run_io_cmd, args=(pod_obj, expect_to_fail,))
    thread.start()
    sleep(2)

    # Checking file existence
    test_file = TEST_FILE + "1"
    assert check_file_existence(pod_obj, test_file), (
        f"I/O failed to start inside {pod_obj.name}"
    )

    return thread


def get_admin_key_from_ceph_tools():
    """
    Fetches admin key secret from ceph
    Returns:
            admin keyring encoded with base64 as a string
    """
    tools_pod = get_ceph_tools_pod()
    out = tools_pod.exec_ceph_cmd(ceph_cmd='ceph auth get-key client.admin')
    base64_output = base64.b64encode(out['key'].encode()).decode()
    return base64_output


def run_io_and_verify_mount_point(pod_obj, bs='10M', count='950'):
    """
    Run I/O on mount point


    Args:
        pod_obj (Pod): The object of the pod
        bs (str): Read and write up to bytes at a time
        count (str): Copy only N input blocks

    Returns:
         used_percentage (str): Used percentage on mount point
    """
    pod_obj.exec_cmd_on_pod(
        command=f"dd if=/dev/urandom of=/var/lib/www/html/dd_a bs={bs} count={count}"
    )

    # Verify data's are written to mount-point
    mount_point = pod_obj.exec_cmd_on_pod(command="df -kh")
    mount_point = mount_point.split()
    used_percentage = mount_point[mount_point.index('/var/lib/www/html') - 1]
    return used_percentage


def get_pods_having_label(label, namespace):
    """
    Fetches pod resources with given label in given namespace

    Args:
        label (str): label which pods might have
        namespace (str): Namespace in which to be looked up

    Return:
        dict: of pod info
    """
    ocp_pod = OCP(kind=constants.POD, namespace=namespace)
    pods = ocp_pod.get(selector=label).get('items')
    return pods


def get_mds_pods(mds_label=constants.MDS_APP_LABEL, namespace=None):
    """
    Fetches info about mds pods in the cluster

    Args:
        mds_label (str): label associated with mds pods
            (default: defaults.MDS_APP_LABEL)
        namespace (str): Namespace in which ceph cluster lives
            (default: defaults.ROOK_CLUSTER_NAMESPACE)

    Returns:
        list : of mds pod objects
    """
    namespace = namespace or config.ENV_DATA['cluster_namespace']
    mdss = get_pods_having_label(mds_label, namespace)
    mds_pods = [Pod(**mds) for mds in mdss]
    return mds_pods


def get_mon_pods(mon_label=constants.MON_APP_LABEL, namespace=None):
    """
    Fetches info about mon pods in the cluster

    Args:
        mon_label (str): label associated with mon pods
            (default: defaults.MON_APP_LABEL)
        namespace (str): Namespace in which ceph cluster lives
            (default: defaults.ROOK_CLUSTER_NAMESPACE)

    Returns:
        list : of mon pod objects
    """
    namespace = namespace or config.ENV_DATA['cluster_namespace']
    mons = get_pods_having_label(mon_label, namespace)
    mon_pods = [Pod(**mon) for mon in mons]
    return mon_pods


def get_mgr_pods(mgr_label=constants.MGR_APP_LABEL, namespace=None):
    """
    Fetches info about mgr pods in the cluster

    Args:
        mgr_label (str): label associated with mgr pods
            (default: defaults.MGR_APP_LABEL)
        namespace (str): Namespace in which ceph cluster lives
            (default: defaults.ROOK_CLUSTER_NAMESPACE)

    Returns:
        list : of mgr pod objects
    """
    namespace = namespace or config.ENV_DATA['cluster_namespace']
    mgrs = get_pods_having_label(mgr_label, namespace)
    mgr_pods = [Pod(**mgr) for mgr in mgrs]
    return mgr_pods


def get_osd_pods(osd_label=constants.OSD_APP_LABEL, namespace=None):
    """
    Fetches info about osd pods in the cluster

    Args:
        osd_label (str): label associated with osd pods
            (default: defaults.OSD_APP_LABEL)
        namespace (str): Namespace in which ceph cluster lives
            (default: defaults.ROOK_CLUSTER_NAMESPACE)

    Returns:
        list : of osd pod objects
    """
    namespace = namespace or config.ENV_DATA['cluster_namespace']
    osds = get_pods_having_label(osd_label, namespace)
    osd_pods = [Pod(**osd) for osd in osds]
    return osd_pods