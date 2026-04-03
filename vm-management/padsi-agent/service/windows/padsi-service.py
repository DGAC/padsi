#
# Copyright (c) 2025-2026 DGAC/DSNA
#
# This file is part of PADSI.
#
# This software is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software.  If not, see <http://www.gnu.org/licenses/>.
#

import asyncio
import os
import sys

import servicemanager
import win32event
import win32service
import win32serviceutil

sys.path.append(os.path.dirname(__file__))
import padsi_agent


class PADSIService(win32serviceutil.ServiceFramework):
    _svc_name_ = "PADSI-system-agent"
    _svc_display_name_ = "PADSI system agent"
    _svc_description_ = "Main system agent to initialize and manage the system"

    def __init__(self, args):
        super().__init__(args)
        self._app=padsi_agent.PadsiAgent()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._app.stop()

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                                servicemanager.PYS_SERVICE_STARTED,
                                (self._svc_name_, 'Service is starting'))
        asyncio.run(self._app.main_run())
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                                servicemanager.PYS_SERVICE_STARTED,
                                (self._svc_name_, 'Service is stopped'))

if __name__ == '__main__':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    win32serviceutil.HandleCommandLine(PADSIService)

