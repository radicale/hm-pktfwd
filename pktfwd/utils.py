import json
import subprocess
import sentry_sdk
import logging
from time import sleep
from shutil import copyfile
from tenacity import retry, wait_fixed, stop_after_attempt, before_sleep_log
from hm_pyhelper.logger import get_logger, LOGLEVEL
from pktfwd.config.region_config_filenames import REGION_CONFIG_FILENAMES


LOGGER = get_logger(__name__)
LOGLEVEL_INT = getattr(logging, LOGLEVEL)
LORA_PKT_FWD_RETRY_SLEEP_SECONDS = 2
LORA_PKT_FWD_MAX_TRIES = 5


def init_sentry(sentry_key, balena_id, balena_app):
    """
    Initialize sentry with balena_app as environment and
    balenda_id as the user's id. If sentry_key is not set,
    do nothing.
    """
    if(sentry_key):
        sentry_sdk.init(sentry_key, environment=balena_app)
        sentry_sdk.set_user({"id": balena_id})


def write_diagnostics(diagnostics_filepath, is_running):
    """
    Write "true" to diagnostics_filepath if pktfwd is running,
    "false" otherwise.
    """
    with open(diagnostics_filepath, 'w') as diagnostics_stream:
        if (is_running):
            diagnostics_stream.write("true")
        else:
            diagnostics_stream.write("false")


def await_system_ready(sleep_seconds):
    """
    Sleep before starting core functions.
    TODO: Get more information about why.
    Original code: https://github.com/NebraLtd/hm-pktfwd/blob/5a0178341e69ecbf6b1dbc8463f6bd1231e9e657/files/configurePktFwd.py#L77  # noqa: E501
    """
    LOGGER.debug("Waiting %s seconds for systems to be ready" % sleep_seconds)
    sleep(sleep_seconds)
    LOGGER.debug("System now ready")


def run_reset_lgw(is_sx1302, sx1301_reset_lgw_filepath,
                  sx1302_reset_lgw_filepath, reset_lgw_pin):
    """
    Invokes reset_lgw.sh script with the reset pin value.
    """
    # Use the correct reset depending on chip version
    reset_lgw_filepath = sx1301_reset_lgw_filepath
    if is_sx1302:
        reset_lgw_filepath = sx1302_reset_lgw_filepath

    # reset_lgw script is expecting a string, not an int
    reset_lgw_pin_str = str(reset_lgw_pin)
    LOGGER.debug("Executing %s with reset pin %s" %
                 (reset_lgw_filepath, reset_lgw_pin_str))

    subprocess.run([reset_lgw_filepath, "stop", reset_lgw_pin_str])
    subprocess.run([reset_lgw_filepath, "start", reset_lgw_pin_str])


def is_concentrator_sx1302(util_chip_id_filepath, spi_bus):
    """
    Use the util_chip_id to determine if concentrator is sx1302.
    util_chip_id calls the sx1302_hal reset_lgw.sh script during execution.
    """
    util_chip_id_cmd = [util_chip_id_filepath, "-d", "/dev/{}".format(spi_bus)]

    try:
        subprocess.run(util_chip_id_cmd, capture_output=True,
                       text=True, check=True).stdout
        return True
    # CalledProcessError raised if there is a non-zero exit code
    # https://docs.python.org/3/library/subprocess.html#using-the-subprocess-module
    except Exception:
        return False


def get_region_filename(region):
    """
    Return filename for config corresponding to region.
    """
    return REGION_CONFIG_FILENAMES[region]


def update_global_conf(is_sx1302, root_dir, sx1301_region_configs_dir,
                       sx1302_region_configs_dir, region, spi_bus):
    """
    Replace global_conf.json with the configuration necessary given
    the concentrator chip type, region, and spi_bus.
    """
    if is_sx1302:
        replace_sx1302_global_conf_with_regional(sx1302_region_configs_dir,
                                                 region, spi_bus)
    else:
        replace_sx1301_global_conf_with_regional(root_dir,
                                                 sx1301_region_configs_dir,
                                                 region)


def replace_sx1301_global_conf_with_regional(root_dir,
                                             sx1301_region_configs_dir,
                                             region):
    """
    Copy the regional configuration file to global_conf.json
    """
    region_config_filepath = "%s/%s" % \
                             (sx1301_region_configs_dir,
                              get_region_filename(region))

    global_config_filepath = "%s/%s" % (root_dir, "global_conf.json")
    LOGGER.debug("Copying SX1301 conf from %s to %s" %
                 (region_config_filepath, global_config_filepath))
    copyfile(region_config_filepath, global_config_filepath)


def replace_sx1302_global_conf_with_regional(sx1302_region_configs_dir,
                                             region, spi_bus):
    """
    Parses the regional configuration file in order to make changes
    and save them to global_conf.json
    """
    # Write the configuration files
    region_config_filepath = "%s/%s" % \
                             (sx1302_region_configs_dir,
                              get_region_filename(region))

    global_config_filepath = "%s/%s" % \
                             (sx1302_region_configs_dir,
                              "global_conf.json")

    with open(region_config_filepath) as region_config_file:
        new_global_conf = json.load(region_config_file)

    # Inject SPI Bus
    new_global_conf['SX130x_conf']['com_dir'] = "/dev/%s" % spi_bus

    with open(global_config_filepath, 'w') as global_config_file:
        json.dump(new_global_conf, global_config_file)


@retry(wait=wait_fixed(LORA_PKT_FWD_RETRY_SLEEP_SECONDS),
       stop=stop_after_attempt(LORA_PKT_FWD_MAX_TRIES),
       before_sleep=before_sleep_log(LOGGER, LOGLEVEL_INT))
def retry_start_concentrator(is_sx1302, spi_bus,
                             sx1302_lora_pkt_fwd_filepath,
                             sx1301_lora_pkt_fwd_dir,
                             sx1301_reset_lgw_filepath,
                             sx1302_reset_lgw_filepath, reset_lgw_pin):
    """
    Retry to start lora_pkt_fwd for the corresponding concentrator model.
    Runs the reset_lgw script before every attempt.
    """
    run_reset_lgw(is_sx1302, sx1301_reset_lgw_filepath,
                  sx1302_reset_lgw_filepath, reset_lgw_pin)

    if is_sx1302:
        subprocess.run(sx1302_lora_pkt_fwd_filepath)
    else:
        sx1301_lora_pkt_fwd_filepath = "%s/lora_pkt_fwd_%s" % \
                                        (sx1301_lora_pkt_fwd_dir, spi_bus)
        subprocess.run(sx1301_lora_pkt_fwd_filepath)
