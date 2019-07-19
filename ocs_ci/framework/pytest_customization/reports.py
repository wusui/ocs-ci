import pytest
import logging
from py.xml import html
from ocs_ci.utility.utils import email_reports
from ocs_ci.framework import config as ocsci_config


@pytest.mark.optionalhook
def pytest_html_results_table_header(cells):
    """
    Add Description header to the table
    """
    cells.insert(2, html.th('Description'))


@pytest.mark.optionalhook
def pytest_html_results_table_row(report, cells):
    """
    Add content to the column Description
    """
    try:
        cells.insert(2, html.td(report.description))
    except AttributeError:
        cells.insert(2, html.td('--- no description ---'))


@pytest.mark.hookwrapper
def pytest_runtest_makereport(item, call):
    """
    Add extra column( Log File) and link the log file location
    """
    pytest_html = item.config.pluginmanager.getplugin('html')
    outcome = yield
    report = outcome.get_result()
    report.description = str(item.function.__doc__)
    extra = getattr(report, 'extra', [])

    if report.when == 'call':
        handlers = logging.getLogger().handlers
        logging.info(str(handlers))
        if len(handlers) > 1:
            log_file = handlers[1].baseFilename
            extra.append(pytest_html.extras.url(log_file, name='Log File'))
            report.extra = extra
        else:
            logging.warn("pytest_runtest_makereport: Logger had less than 2 handlers!")
            logging.warn(f"item.function.__name__: {item.function.__name__}")
            logging.warn(f"item.name: {item.name}")


def pytest_sessionfinish(session, exitstatus):
    """
    send email report
    """
    if ocsci_config.RUN['cli_params']['email']:
        email_reports()
