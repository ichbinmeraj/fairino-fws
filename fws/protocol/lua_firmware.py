"""Measured Lua capability, per firmware — GENERATED. Do not edit by hand.


HOW IT WAS MEASURED. A one-line program calling the function is uploaded; the
controller compiles it and writes its verdict to the log, which RbLogDownload
fetches. Nothing is ever loaded or run.

WHAT `present` MEANS. The compiler resolved the name to something callable.
It is False ONLY when the compiler said `attempt to call global X (a nil
value)` -- the name does not exist on this firmware, whatever the manual says.

`status` is the finer answer:

    ok             compiled clean at the documented argument count
    wrong_arity    the function exists; the MANUAL's count is not accepted
    present_other  the function exists and the call failed for another
                   reason -- most often `failed to query the database`,
                   which is what a point-name function does when handed a
                   name that is not in the teaching database
    absent         not on this firmware

`present_other` is why this file exists in its current form. An earlier probe
inferred absence from "rejected at every arity" and reported Lin, ARC, Circle,
DMP and the Modbus family as missing. All of them are present; they take a
point name and fail the lookup at every arity. Ten names were wrong.

WHAT IT DOES NOT MEAN. Not that a call is safe, does what the manual says, or
that arguments are in the order you expect.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LuaAvailability:
    present: bool
    status: str
    documented_arity: int | None
    probed_arity: int | None
    detail: str | None

    @property
    def manual_arity_accepted(self) -> bool | None:
        """None when there was no documented arity to test."""
        if self.documented_arity is None or not self.present:
            return None
        return self.status == "ok"



LUA_FIRMWARE: dict[str, dict[str, LuaAvailability]] = {
    'v3.8.5.1': {
        'ARC': LuaAvailability(present=True, status='present_other', documented_arity=18, probed_arity=18, detail='failed to query the database (the data does not exist)'),
        'ARCEnd': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'ARCStart': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'ActGripper': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'ArcWeldTraceControl': LuaAvailability(present=True, status='ok', documented_arity=17, probed_arity=17, detail=None),
        'ArcWeldTraceReplayEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'ArcWeldTraceReplayStart': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'AuxServoEnable': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'AuxServoHoming': LuaAvailability(present=True, status='wrong_arity', documented_arity=4, probed_arity=4, detail='AuxServoHoming'),
        'AuxServoSetControlmode': LuaAvailability(present=False, status='absent', documented_arity=2, probed_arity=2, detail='AuxServoSetControlmode'),
        'AuxServoSetStatusID': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'AuxServoSetTargetSpeed': LuaAvailability(present=True, status='wrong_arity', documented_arity=2, probed_arity=2, detail='AuxServoSetTargetSpeed'),
        'Circle': LuaAvailability(present=True, status='ok', documented_arity=51, probed_arity=51, detail=None),
        'ClearDexterousHandsActError': LuaAvailability(present=False, status='absent', documented_arity=None, probed_arity=1, detail='ClearDexterousHandsActError'),
        'ConveyorGetTrackData': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'ConveyorIODetect': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'ConveyorTrackEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'ConveyorTrackStart': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'CustomCollisionDetectionEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'CustomCollisionDetectionStart': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'DMP': LuaAvailability(present=True, status='present_other', documented_arity=2, probed_arity=2, detail='failed to query the database (the data does not exist)'),
        'DmpMotion': LuaAvailability(present=False, status='absent', documented_arity=None, probed_arity=1, detail='DmpMotion'),
        'DofileEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'EXT_AXIS_PTP': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ExtAxisMoveJ': LuaAvailability(present=True, status='ok', documented_arity=6, probed_arity=6, detail=None),
        'ExtAxisServoOn': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'ExtAxisSetHoming': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'ExtDevLoadUDPDriver': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'ExtDevSetUDPComParam': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'FT_CalCenterEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'FT_CalCenterStart': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'FT_Click': LuaAvailability(present=True, status='wrong_arity', documented_arity=4, probed_arity=4, detail='FT_Click'),
        'FT_ComplianceStart': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'FT_ComplianceStop': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'FT_Control': LuaAvailability(present=True, status='wrong_arity', documented_arity=21, probed_arity=21, detail='FT_Control'),
        'FT_FindSurface': LuaAvailability(present=True, status='ok', documented_arity=7, probed_arity=7, detail=None),
        'FT_Guard': LuaAvailability(present=True, status='ok', documented_arity=26, probed_arity=26, detail=None),
        'FT_LinInsertion': LuaAvailability(present=True, status='ok', documented_arity=6, probed_arity=6, detail=None),
        'FT_RotInsertion': LuaAvailability(present=True, status='ok', documented_arity=7, probed_arity=7, detail=None),
        'FT_SpiralSearch': LuaAvailability(present=True, status='ok', documented_arity=5, probed_arity=5, detail=None),
        'FieldBusSlaveReadAI': LuaAvailability(present=True, status='wrong_arity', documented_arity=3, probed_arity=3, detail='FieldBusSlaveReadAI'),
        'FieldBusSlaveReadDI': LuaAvailability(present=True, status='wrong_arity', documented_arity=3, probed_arity=3, detail='FieldBusSlaveReadDI'),
        'FieldBusSlaveWaitAI': LuaAvailability(present=True, status='wrong_arity', documented_arity=3, probed_arity=3, detail='FieldBusSlaveWaitAI'),
        'FieldBusSlaveWaitDI': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'FieldBusSlaveWriteAO': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'FieldBusSlaveWriteDO': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'GetAI': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'GetAO': LuaAvailability(present=False, status='absent', documented_arity=2, probed_arity=2, detail='GetAO'),
        'GetActualTCPNum': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'GetActualWObjNum': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'GetAuxAI': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'GetAuxAO': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='GetAuxAO'),
        'GetAuxDI': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'GetAuxDO': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='GetAuxDO'),
        'GetAxleGenComCycleData': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='GetAxleGenComCycleData'),
        'GetDI': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'GetDO': LuaAvailability(present=False, status='absent', documented_arity=2, probed_arity=2, detail='GetDO'),
        'GetInverseKinHasSolution': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'GetInverseKinRef': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'GetLaserWeldingParamActual': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='GetLaserWeldingParamActual'),
        'GetLaserWeldingParamTarget': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='GetLaserWeldingParamTarget'),
        'GetLaserWeldingRunningState': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='GetLaserWeldingRunningState'),
        'GetSuckerState': LuaAvailability(present=True, status='ok', documented_arity=None, probed_arity=1, detail=None),
        'GetSysVarvalue': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='GetSysVarvalue'),
        'GetToolAI': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'GetToolAO': LuaAvailability(present=False, status='absent', documented_arity=2, probed_arity=2, detail='GetToolAO'),
        'GetToolDI': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'GetToolDO': LuaAvailability(present=False, status='absent', documented_arity=2, probed_arity=2, detail='GetToolDO'),
        'GetTrajectoryPointNum': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'GetTrajectoryStartPose': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'GetVirtualAI': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'GetVirtualDI': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'GetVirtualToolAI': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'GetVirtualToolDI': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'GetWireSearchOffset': LuaAvailability(present=True, status='wrong_arity', documented_arity=4, probed_arity=4, detail='GetWireSearchOffset'),
        'HorizonSpiralMotionEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'HorizonSpiralMotionStart': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'ImpedanceControlStartStop': LuaAvailability(present=False, status='absent', documented_arity=0, probed_arity=0, detail='ImpedanceControlStartStop'),
        'LTLaserOff': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'LTLaserOn': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'LTSearchStart': LuaAvailability(present=True, status='ok', documented_arity=6, probed_arity=6, detail=None),
        'LTSearchStop': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'LTTrackOff': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'LTTrackOn': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'LaserSensorRecord': LuaAvailability(present=True, status='wrong_arity', documented_arity=10, probed_arity=10, detail='LaserSensorRecord'),
        'Lin': LuaAvailability(present=True, status='present_other', documented_arity=11, probed_arity=11, detail='failed to query the database (the data does not exist)'),
        'LoadPosSensorDriver': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'LoadTPD': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'LoadTrajectory': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'LoadTrajectoryJ': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'MatrixGetCount': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='MatrixGetCount'),
        'MatrixMoveEnd': LuaAvailability(present=False, status='absent', documented_arity=0, probed_arity=0, detail='MatrixMoveEnd'),
        'MatrixMoveStart': LuaAvailability(present=False, status='absent', documented_arity=3, probed_arity=3, detail='MatrixMoveStart'),
        'MatrixSetCountPlus': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='MatrixSetCountPlus'),
        'MatrixSetStartCount': LuaAvailability(present=False, status='absent', documented_arity=4, probed_arity=4, detail='MatrixSetStartCount'),
        'ModbusMasterReadAI': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterReadAI_RTU': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterReadAO': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterReadAO_RTU': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterReadDI': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterReadDI_RTU': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterReadDO': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterReadDO_RTU': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterReadReg_RTU': LuaAvailability(present=True, status='wrong_arity', documented_arity=5, probed_arity=5, detail='ModbusMasterReadReg_RTU'),
        'ModbusMasterWaitAI': LuaAvailability(present=True, status='present_other', documented_arity=5, probed_arity=5, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterWaitAI_RTU': LuaAvailability(present=True, status='present_other', documented_arity=5, probed_arity=5, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterWaitDI': LuaAvailability(present=True, status='present_other', documented_arity=4, probed_arity=4, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterWaitDI_RTU': LuaAvailability(present=True, status='present_other', documented_arity=4, probed_arity=4, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterWriteAO': LuaAvailability(present=True, status='present_other', documented_arity=4, probed_arity=4, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterWriteAO_RTU': LuaAvailability(present=True, status='present_other', documented_arity=4, probed_arity=4, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterWriteDO': LuaAvailability(present=True, status='present_other', documented_arity=4, probed_arity=4, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterWriteDO_RTU': LuaAvailability(present=True, status='present_other', documented_arity=4, probed_arity=4, detail='failed to query the database (the data does not exist)'),
        'ModbusMasterWriteReg_RTU': LuaAvailability(present=True, status='wrong_arity', documented_arity=None, probed_arity=1, detail='ModbusMasterWriteReg_RTU'),
        'ModbusMasterWrite_RTU': LuaAvailability(present=False, status='absent', documented_arity=6, probed_arity=6, detail='ModbusMasterWrite_RTU'),
        'ModbusRegGetData': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'ModbusRegRead': LuaAvailability(present=True, status='ok', documented_arity=5, probed_arity=5, detail=None),
        'ModbusRegWrite': LuaAvailability(present=True, status='ok', documented_arity=6, probed_arity=6, detail=None),
        'ModbusSlaveReadDI_RTU': LuaAvailability(present=True, status='wrong_arity', documented_arity=None, probed_arity=1, detail='ModbusSlaveReadDI_RTU'),
        'ModbusSlaveReadDO': LuaAvailability(present=True, status='present_other', documented_arity=2, probed_arity=2, detail='failed to query the database (the data does not exist)'),
        'ModbusSlaveReadDO_RTU': LuaAvailability(present=True, status='present_other', documented_arity=2, probed_arity=2, detail='failed to query the database (the data does not exist)'),
        'ModbusSlaveWaitAI': LuaAvailability(present=True, status='present_other', documented_arity=4, probed_arity=4, detail='failed to query the database (the data does not exist)'),
        'ModbusSlaveWaitAI_RTU': LuaAvailability(present=True, status='present_other', documented_arity=4, probed_arity=4, detail='failed to query the database (the data does not exist)'),
        'ModbusSlaveWaitDI': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusSlaveWaitDI_RTU': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusSlaveWriteDO': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'ModbusSlaveWriteDO_RTU': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'MoveAOStart': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'MoveAOStop': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'MoveC': LuaAvailability(present=True, status='ok', documented_arity=56, probed_arity=56, detail=None),
        'MoveCart': LuaAvailability(present=True, status='ok', documented_arity=8, probed_arity=8, detail=None),
        'MoveDOStart': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'MoveDOStop': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'MoveGripper': LuaAvailability(present=True, status='ok', documented_arity=6, probed_arity=6, detail=None),
        'MoveIntersectLine': LuaAvailability(present=False, status='absent', documented_arity=21, probed_arity=21, detail='MoveIntersectLine'),
        'MoveJ': LuaAvailability(present=True, status='ok', documented_arity=29, probed_arity=29, detail=None),
        'MoveL': LuaAvailability(present=True, status='ok', documented_arity=33, probed_arity=33, detail=None),
        'MoveLTR': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'MoveStationary': LuaAvailability(present=False, status='absent', documented_arity=0, probed_arity=0, detail='MoveStationary'),
        'MoveTPD': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'MoveToIntersectLineStart': LuaAvailability(present=False, status='absent', documented_arity=19, probed_arity=19, detail='MoveToIntersectLineStart'),
        'MoveToLaserRecordEnd': LuaAvailability(present=True, status='wrong_arity', documented_arity=0, probed_arity=0, detail='MoveToLaserRecordEnd'),
        'MoveToLaserRecordStart': LuaAvailability(present=True, status='wrong_arity', documented_arity=0, probed_arity=0, detail='MoveToLaserRecordStart'),
        'MoveToTPDStart': LuaAvailability(present=False, status='absent', documented_arity=3, probed_arity=3, detail='MoveToTPDStart'),
        'MoveToolAOStart': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'MoveToolAOStop': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'MoveToolDOStart': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'MoveToolDOStop': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'MoveTrajectory': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'MoveTrajectoryJ': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'MultilayerOffsetTrsfToBase': LuaAvailability(present=True, status='wrong_arity', documented_arity=12, probed_arity=12, detail='MultilayerOffsetTrsfToBase'),
        'MultiplayerOffsetTrsfToBase': LuaAvailability(present=False, status='absent', documented_arity=None, probed_arity=1, detail='MultiplayerOffsetTrsfToBase'),
        'NewAuxThread': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'NewDofile': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'NewSP': LuaAvailability(present=True, status='present_other', documented_arity=4, probed_arity=4, detail='failed to query the database (the data does not exist)'),
        'NewSpiral': LuaAvailability(present=True, status='wrong_arity', documented_arity=17, probed_arity=17, detail='NewSpiral'),
        'NewSplineEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'NewSplinePoint': LuaAvailability(present=True, status='wrong_arity', documented_arity=18, probed_arity=18, detail='NewSplinePoint'),
        'NewSplineStart': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'OriginPointWeaveEnd': LuaAvailability(present=False, status='absent', documented_arity=0, probed_arity=0, detail='OriginPointWeaveEnd'),
        'OriginPointWeaveStart': LuaAvailability(present=False, status='absent', documented_arity=4, probed_arity=4, detail='OriginPointWeaveStart'),
        'PTP': LuaAvailability(present=True, status='present_other', documented_arity=10, probed_arity=10, detail='failed to query the database (the data does not exist)'),
        'Pause': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'PointTableSwitch': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'PointTableUpdateLua': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='PointTableUpdateLua'),
        'PointsOffsetDisable': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'PointsOffsetEnable': LuaAvailability(present=True, status='ok', documented_arity=7, probed_arity=7, detail=None),
        'PolishingClearError': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'PolishingDeviceEnable': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'PolishingLoadComDriver': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'PolishingSetTargetPosition': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'PolishingSetTargetTorque': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'PolishingSetTargetTouchForce': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'PolishingSetTargetTouchForceTime': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='PolishingSetTargetTouchForceTime'),
        'PolishingSetTargetVelocity': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'PolishingSetWorkPieceWeight': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'PolishingTorqueSensorReset': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'PolishingUnloadComDriver': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'PostureAdjustOff': LuaAvailability(present=True, status='wrong_arity', documented_arity=0, probed_arity=0, detail='PostureAdjustOff'),
        'PostureAdjustOn': LuaAvailability(present=True, status='present_other', documented_arity=11, probed_arity=11, detail='failed to query the database (the data does not exist)'),
        'PowerCleanStart': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'PowerCleanStop': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'PrintMsg': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='PrintMsg'),
        'RegisterVar': LuaAvailability(present=True, status='wrong_arity', documented_arity=2, probed_arity=2, detail='RegisterVar'),
        'ResetLaserWeldingErr': LuaAvailability(present=False, status='absent', documented_arity=2, probed_arity=2, detail='ResetLaserWeldingErr'),
        'SCIRC': LuaAvailability(present=True, status='present_other', documented_arity=3, probed_arity=3, detail='failed to query the database (the data does not exist)'),
        'SLIN': LuaAvailability(present=True, status='present_other', documented_arity=2, probed_arity=2, detail='failed to query the database (the data does not exist)'),
        'SPLCGetAI': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'SPLCGetDI': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'SPLCGetToolAI': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'SPLCGetToolDI': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'SPLCSetAO': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SPLCSetDO': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SPLCSetToolAO': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SPLSetToolDO': LuaAvailability(present=False, status='absent', documented_arity=4, probed_arity=4, detail='SPLSetToolDO'),
        'SPTP': LuaAvailability(present=True, status='present_other', documented_arity=2, probed_arity=2, detail='failed to query the database (the data does not exist)'),
        'ServoCart': LuaAvailability(present=True, status='wrong_arity', documented_arity=19, probed_arity=19, detail='ServoCart'),
        'ServoJ': LuaAvailability(present=True, status='ok', documented_arity=15, probed_arity=15, detail=None),
        'ServoMoveEnd': LuaAvailability(present=False, status='absent', documented_arity=0, probed_arity=0, detail='ServoMoveEnd'),
        'ServoMoveStart': LuaAvailability(present=False, status='absent', documented_arity=0, probed_arity=0, detail='ServoMoveStart'),
        'SetAO': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'SetAnticollision': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'SetAspirated': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SetAuxAO': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'SetAuxDO': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'SetDFCForce': LuaAvailability(present=False, status='absent', documented_arity=2, probed_arity=2, detail='SetDFCForce'),
        'SetDO': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'SetDexterousHandsAct': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='SetDexterousHandsAct'),
        'SetDexterousHandsMove': LuaAvailability(present=False, status='absent', documented_arity=5, probed_arity=5, detail='SetDexterousHandsMove'),
        'SetEversewireFeed': LuaAvailability(present=False, status='absent', documented_arity=None, probed_arity=1, detail='SetEversewireFeed'),
        'SetForwardWireFeed': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SetLaserWeldingEnable': LuaAvailability(present=False, status='absent', documented_arity=None, probed_arity=1, detail='SetLaserWeldingEnable'),
        'SetLaserWeldingEnableExtDoNum': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='SetLaserWeldingEnableExtDoNum'),
        'SetLaserWeldingErrStateExtDiNum': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='SetLaserWeldingErrStateExtDiNum'),
        'SetLaserWeldingParam': LuaAvailability(present=False, status='absent', documented_arity=7, probed_arity=7, detail='SetLaserWeldingParam'),
        'SetLaserWeldingRunningStateExtDiNum': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='SetLaserWeldingRunningStateExtDiNum'),
        'SetLaserWeldingStart': LuaAvailability(present=False, status='absent', documented_arity=3, probed_arity=3, detail='SetLaserWeldingStart'),
        'SetLaserWeldingStartExtDoNum': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='SetLaserWeldingStartExtDoNum'),
        'SetOaccScale': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'SetPointToDatabase': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SetReverseWireFeed': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SetStationTrackPara': LuaAvailability(present=False, status='absent', documented_arity=0, probed_arity=0, detail='SetStationTrackPara'),
        'SetSuckerCtrl': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'SetSysVarvalue': LuaAvailability(present=False, status='absent', documented_arity=2, probed_arity=2, detail='SetSysVarvalue'),
        'SetToolAO': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'SetToolDO': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'SetToolList': LuaAvailability(present=True, status='present_other', documented_arity=1, probed_arity=1, detail='failed to query the database (the data does not exist)'),
        'SetVirtualAI': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SetVirtualDI': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SetVirtualToolAI': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SetVirtualToolDI': LuaAvailability(present=True, status='ok', documented_arity=2, probed_arity=2, detail=None),
        'SetWObjList': LuaAvailability(present=True, status='present_other', documented_arity=1, probed_arity=1, detail='failed to query the database (the data does not exist)'),
        'SndRcvAxleGenComCmdData': LuaAvailability(present=False, status='absent', documented_arity=1, probed_arity=1, detail='SndRcvAxleGenComCmdData'),
        'Spiral': LuaAvailability(present=True, status='present_other', documented_arity=17, probed_arity=17, detail='failed to query the database (the data does not exist)'),
        'SplineCIRC': LuaAvailability(present=True, status='ok', documented_arity=33, probed_arity=33, detail=None),
        'SplineEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'SplineLINE': LuaAvailability(present=True, status='ok', documented_arity=17, probed_arity=17, detail=None),
        'SplinePTP': LuaAvailability(present=True, status='ok', documented_arity=17, probed_arity=17, detail=None),
        'SplineStart': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'SprayStart': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'SprayStop': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'ToolTrsfEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'ToolTrsfStart': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'TorqueRecordEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'TorqueRecordReset': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'TorqueRecordStart': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'UnloadPosSensorDriver': LuaAvailability(present=True, status='wrong_arity', documented_arity=1, probed_arity=1, detail='UnloadPosSensorDriver'),
        'WaitAI': LuaAvailability(present=True, status='ok', documented_arity=5, probed_arity=5, detail=None),
        'WaitAuxAI': LuaAvailability(present=True, status='ok', documented_arity=5, probed_arity=5, detail=None),
        'WaitAuxDI': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'WaitDI': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'WaitMs': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'WaitMultiDI': LuaAvailability(present=True, status='ok', documented_arity=5, probed_arity=5, detail=None),
        'WaitStationaryMotionDone': LuaAvailability(present=False, status='absent', documented_arity=0, probed_arity=0, detail='WaitStationaryMotionDone'),
        'WaitSuckerState': LuaAvailability(present=True, status='ok', documented_arity=3, probed_arity=3, detail=None),
        'WaitToolAI': LuaAvailability(present=True, status='ok', documented_arity=5, probed_arity=5, detail=None),
        'WaitToolDI': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'WeaveChangeEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'WeaveChangeStart': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'WeaveEnd': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'WeaveEndSim': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'WeaveInspectEnd': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'WeaveInspectStart': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'WeaveStart': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'WeaveStartSim': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'WeldingGetCurrentRelation': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'WeldingGetVoltageRelation': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'WeldingSetCurrent': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'WeldingSetCurrentGradualChangeEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'WeldingSetCurrentGradualChangeStart': LuaAvailability(present=True, status='ok', documented_arity=5, probed_arity=5, detail=None),
        'WeldingSetCurrertRelation': LuaAvailability(present=False, status='absent', documented_arity=5, probed_arity=5, detail='WeldingSetCurrertRelation'),
        'WeldingSetProcessParam': LuaAvailability(present=True, status='ok', documented_arity=9, probed_arity=9, detail=None),
        'WeldingSetVoltage': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'WeldingSetVoltageGradualChangeEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'WeldingSetVoltageGradualChangeStart': LuaAvailability(present=True, status='ok', documented_arity=5, probed_arity=5, detail=None),
        'WeldingSetVoltageRelation': LuaAvailability(present=True, status='ok', documented_arity=5, probed_arity=5, detail=None),
        'WireSearchEnd': LuaAvailability(present=True, status='ok', documented_arity=7, probed_arity=7, detail=None),
        'WireSearchStart': LuaAvailability(present=True, status='ok', documented_arity=7, probed_arity=7, detail=None),
        'WireSearchWait': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'WorkPieceTrsfEnd': LuaAvailability(present=True, status='ok', documented_arity=0, probed_arity=0, detail=None),
        'WorkPieceTrsfStart': LuaAvailability(present=True, status='ok', documented_arity=1, probed_arity=1, detail=None),
        'XmlrpcClientCall': LuaAvailability(present=True, status='ok', documented_arity=4, probed_arity=4, detail=None),
        'dmpMotion': LuaAvailability(present=True, status='ok', documented_arity=8, probed_arity=8, detail=None),
    },
}

# 282 names, 290 uploads, nothing executed.
# {'present_other': 37, 'ok': 173, 'wrong_arity': 21, 'absent': 51}


def availability(name: str, version: str) -> LuaAvailability | None:
    """What we measured for `name` on `version`, or None.

    None means UNKNOWN, never 'absent'. FWS has probed the
    firmwares it was pointed at and no others.
    """
    return LUA_FIRMWARE.get(version, {}).get(name)


def probed_versions() -> list[str]:
    return sorted(LUA_FIRMWARE)


def absent_on(version: str) -> list[str]:
    """Documented by Fairino, not present on this firmware."""
    return sorted(n for n, a in LUA_FIRMWARE.get(version, {}).items()
                  if not a.present)


def arity_disagrees_on(version: str) -> dict[str, int | None]:
    """Present, but the manual's argument count is rejected.

    Each of these is a program that would fail to upload if
    written from the manual.
    """
    return {n: a.documented_arity
            for n, a in LUA_FIRMWARE.get(version, {}).items()
            if a.status == 'wrong_arity'}


def needs_a_taught_point(version: str) -> list[str]:
    """Present, but failed on a teaching-database lookup.

    These take a point NAME. On a controller with no teaching
    points they compile only once a real name exists -- which is
    a capability statement about your cell, not about the
    firmware.
    """
    return sorted(n for n, a in LUA_FIRMWARE.get(version, {}).items()
                  if a.status == 'present_other'
                  and a.detail and 'database' in a.detail)
