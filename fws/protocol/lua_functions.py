"""FR Lua preset functions -- GENERATED. Do not edit by hand.


This is the surface available INSIDE a Lua program running on the
controller. It is not the XML-RPC surface: the two overlap, disagree on
argument order in at least one dangerous place, and each has functions
the other lacks. See LUA_ONLY / RPC_ONLY in fws/protocol/lua_bridge.py.

`prototype` is quoted from the manual verbatim. `arity` is counted from
it. Neither is evidence the function exists on your firmware -- the
manual documents a later release than v3.8.5.1.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LuaFunction:
    name: str
    section: str
    prototype: str
    arity: int | None
    brief: str


LUA_FUNCTIONS: dict[str, LuaFunction] = {
    'ARC': LuaFunction(
        name='ARC', section='3.2.3 Arc',
        prototype='ARC(point_p_name, poffset, offset_px, offset_py, offset_pz, offset_prx, offset_pry, offset_prz, point_t_name, toffset, offset_tx, offset_ty, offset_tz, offset_trx, offset_try, offset_trz, ovl, blend)',
        arity=18, brief='Arc Motion'),
    'ARCEnd': LuaFunction(
        name='ARCEnd', section='3.5.1 Welding',
        prototype='ARCEnd(ioType, arcNum, timeout)',
        arity=3, brief='End Arc'),
    'ARCStart': LuaFunction(
        name='ARCStart', section='3.5.1 Welding',
        prototype='ARCStart(ioType, arcNum, timeout)',
        arity=3, brief='Start Arc'),
    'ActGripper': LuaFunction(
        name='ActGripper', section='3.4.1 Gripper',
        prototype='ActGripper(index,action)',
        arity=2, brief=''),
    'ArcWeldTraceControl': LuaFunction(
        name='ArcWeldTraceControl', section='3.5.2 Arc Tracking',
        prototype='ArcWeldTraceControl(flag, delaytime, isLeftRight, klr, tStartLr, stepmaxLr, summaxLr, isUpLow, kud, tStartUd, stepmaxUd, summaxUd, axisSelect, referenceType, referSampleStartUd, referSampleCountUd, referenceCurrent)',
        arity=17, brief=''),
    'ArcWeldTraceReplayEnd': LuaFunction(
        name='ArcWeldTraceReplayEnd', section='3.5.2 Arc Tracking',
        prototype='ArcWeldTraceReplayEnd( )',
        arity=0, brief=''),
    'ArcWeldTraceReplayStart': LuaFunction(
        name='ArcWeldTraceReplayStart', section='3.5.2 Arc Tracking',
        prototype='ArcWeldTraceReplayStart( )',
        arity=0, brief='Arc tracking with multi-layer and multi-channel'),
    'AuxServoEnable': LuaFunction(
        name='AuxServoEnable', section='3.4.3 Expansion axis',
        prototype='AuxServoEnable(servoid, status)',
        arity=2, brief=''),
    'AuxServoHoming': LuaFunction(
        name='AuxServoHoming', section='3.4.3 Expansion axis',
        prototype='AuxServoHoming(servoid, mode, searchVel, latchVel)',
        arity=4, brief='Set 485 extension axis return to zero mode'),
    'AuxServoSetControlmode': LuaFunction(
        name='AuxServoSetControlmode', section='3.4.3 Expansion axis',
        prototype='AuxServoSetControlmode(servoid, mode)',
        arity=2, brief=''),
    'AuxServoSetStatusID': LuaFunction(
        name='AuxServoSetStatusID', section='3.4.3 Expansion axis',
        prototype='AuxServoSetStatusID(servoid)',
        arity=1, brief='Set the 485 extension axis data axis number in the status'),
    'AuxServoSetTargetSpeed': LuaFunction(
        name='AuxServoSetTargetSpeed', section='3.4.3 Expansion axis',
        prototype='AuxServoSetTargetSpeed(servoid, speed)',
        arity=2, brief=''),
    'Circle': LuaFunction(
        name='Circle', section='3.2.4 Complete Circle',
        prototype='Circle(pj1, pj2, pj3, pj4, pj5, pj6, px, py, pz, prx, pry, prz, ptool, puser, pspeed, pacc, pep1, pep2, pep3, pep4, tj1, tj2, tj3, tj4, tj5, tj6, tx, ty, tz, trx, try, trz, ttool, tuser, tspeed, tacc, tep1, tep2, tep3, tep4, ovl, offset, offset_x, offset_y, offset_z, offset_rx, offset_ry, offset_rz, velAccParamMode, speed, acc)',
        arity=51, brief='Complete circular motion (Cartesian space)'),
    'ClearDexterousHandsActError': LuaFunction(
        name='ClearDexterousHandsActError', section='?',
        prototype='',
        arity=None, brief='Fix the issue with the nimble hand'),
    'ConveyorGetTrackData': LuaFunction(
        name='ConveyorGetTrackData', section='3.4.6 Suction cups',
        prototype='ConveyorGetTrackData(slaveID)',
        arity=1, brief=''),
    'ConveyorIODetect': LuaFunction(
        name='ConveyorIODetect', section='3.4.4 Conveyor Belt',
        prototype='ConveyorIODetect(max_t)',
        arity=1, brief='IO real-time detection'),
    'ConveyorTrackEnd': LuaFunction(
        name='ConveyorTrackEnd', section='3.4.4 Conveyor Belt',
        prototype='ConveyorTrackEnd()',
        arity=0, brief='Stop belt tracking'),
    'ConveyorTrackStart': LuaFunction(
        name='ConveyorTrackStart', section='3.4.4 Conveyor Belt',
        prototype='ConveyorTrackStart(status)',
        arity=1, brief='Enable belt tracking'),
    'CustomCollisionDetectionEnd': LuaFunction(
        name='CustomCollisionDetectionEnd', section='3.3.10 Collision Detection',
        prototype='CustomCollisionDetectionEnd()',
        arity=0, brief='Collision threshold function disabled'),
    'CustomCollisionDetectionStart': LuaFunction(
        name='CustomCollisionDetectionStart', section='3.3.10 Collision Detection',
        prototype='CustomCollisionDetectionStart(flag,jointDetectionThreshold,tcpDetectionThres hold, block)',
        arity=4, brief='Collision threshold function enabled'),
    'DMP': LuaFunction(
        name='DMP', section='3.2.16 DMP',
        prototype='DMP(point_name, ovl)',
        arity=2, brief='Trajectory Imitation'),
    'DmpMotion': LuaFunction(
        name='DmpMotion', section='?',
        prototype='',
        arity=None, brief='Trajectory imitation'),
    'DofileEnd': LuaFunction(
        name='DofileEnd', section='3.1.4 Subroutines',
        prototype='DofileEnd()',
        arity=0, brief='Subroutine call ends'),
    'EXT_AXIS_PTP': LuaFunction(
        name='EXT_AXIS_PTP', section='3.4.3 Expansion axis',
        prototype='EXT_AXIS_PTP(mode, name, Vel)',
        arity=3, brief='UDP mode Extended Axis Motion'),
    'ExtAxisMoveJ': LuaFunction(
        name='ExtAxisMoveJ', section='3.4.3 Expansion axis',
        prototype='ExtAxisMoveJ(mode, E1, E2, E3, E4, Vel)',
        arity=6, brief='UDP mode Extended Axis Motion'),
    'ExtAxisServoOn': LuaFunction(
        name='ExtAxisServoOn', section='3.4.3 Expansion axis',
        prototype='ExtAxisServoOn(axisID, status)',
        arity=2, brief='UDP Extended Axis Enable'),
    'ExtAxisSetHoming': LuaFunction(
        name='ExtAxisSetHoming', section='3.4.3 Expansion axis',
        prototype='ExtAxisSetHoming(axisID, mode, searchVel , latchVel)',
        arity=4, brief='UDP Extended Axis returns to Zero'),
    'ExtDevLoadUDPDriver': LuaFunction(
        name='ExtDevLoadUDPDriver', section='3.3.6 Expanding IO',
        prototype='ExtDevLoadUDPDriver()',
        arity=0, brief=''),
    'ExtDevSetUDPComParam': LuaFunction(
        name='ExtDevSetUDPComParam', section='3.3.6 Expanding IO',
        prototype='ExtDevSetUDPComParam(ip, port, period)',
        arity=3, brief='Configure UDP communication data'),
    'FT_CalCenterEnd': LuaFunction(
        name='FT_CalCenterEnd', section='3.6.1 Force Control Set',
        prototype='FT_CalCenterEnd()',
        arity=0, brief='End of calculating the position of the middle plane'),
    'FT_CalCenterStart': LuaFunction(
        name='FT_CalCenterStart', section='3.6.1 Force Control Set',
        prototype='FT_CalCenterStart()',
        arity=0, brief='Start calculating the position of the middle plane'),
    'FT_Click': LuaFunction(
        name='FT_Click', section='3.6.1 Force Control Set',
        prototype='FT_Click(ft, lin_v, lin_a, dismax)',
        arity=4, brief='Tap Force Detection'),
    'FT_ComplianceStart': LuaFunction(
        name='FT_ComplianceStart', section='3.6.1 Force Control Set',
        prototype='FT_ComplianceStart(p, force)',
        arity=2, brief='Smooth control enabled'),
    'FT_ComplianceStop': LuaFunction(
        name='FT_ComplianceStop', section='3.6.1 Force Control Set',
        prototype='FT_ComplianceStop()',
        arity=0, brief='Smooth Control Off'),
    'FT_Control': LuaFunction(
        name='FT_Control', section='3.6.1 Force Control Set',
        prototype='FT_Control(flag, sensor_num, select, force_torque, gain, adj_sign, ILC_sign,max_dis,max_ang, polishRadio, filter_Sign, posAdapt_sign, M0,M1,B0,B1, Threshold1, Threshold2, adjustCoeff1,adjustCoeff2, isNoBlock)',
        arity=21, brief='Constant Force Control'),
    'FT_FindSurface': LuaFunction(
        name='FT_FindSurface', section='3.6.1 Force Control Set',
        prototype='FT_FindSurface(rcs, dir, axis, lin_v, lin_a , dismax, ft)',
        arity=7, brief=''),
    'FT_Guard': LuaFunction(
        name='FT_Guard', section='3.6.1 Force Control Set',
        prototype='FT_Guard(flag, tool_id, select_Fx, select_Fy, select_Fz, select_Tx, select_TY, select_Tz, value_Fx, value_Fy, value_Fz, value_Tx, value_TY, value_Tz , max_threshold_Fx,max_threshold_Fy,max_threshold_Fz,max_threshold_Tx, max_threshold_Ty,max_threshold_Tz, min_threshold_Fx, min_threshold_Fy, min_threshold_Fz, min_threshold_Tx, min_threshold_Ty, min_threshold_Tz)',
        arity=26, brief='Collision Detection'),
    'FT_LinInsertion': LuaFunction(
        name='FT_LinInsertion', section='3.6.1 Force Control Set',
        prototype='FT_LinInsertion(rcs, ft, lin_v , lin_a , dismax, linorn)',
        arity=6, brief='Linear Insertion'),
    'FT_RotInsertion': LuaFunction(
        name='FT_RotInsertion', section='3.6.1 Force Control Set',
        prototype='FT_RotInsertion(rcs, angVelRot, ft,max_angle, orn,max_angAcc, rotorn)',
        arity=7, brief='Rotating Insertion'),
    'FT_SpiralSearch': LuaFunction(
        name='FT_SpiralSearch', section='3.6.1 Force Control Set',
        prototype='FT_SpiralSearch(rcs, dr,ft ,max_t_ms,max_vel)',
        arity=5, brief='Spiral Insertion'),
    'FieldBusSlaveReadAI': LuaFunction(
        name='FieldBusSlaveReadAI', section='3.7.3 Board Card',
        prototype='FieldBusSlaveReadAI(DIStartIndex, writeNum, status)',
        arity=3, brief='Obtain slave AI from the slave mode'),
    'FieldBusSlaveReadDI': LuaFunction(
        name='FieldBusSlaveReadDI', section='3.7.3 Board Card',
        prototype='FieldBusSlaveReadDI(DIStartIndex, writeNum, status)',
        arity=3, brief='Obtain the slave DI from the slave mode'),
    'FieldBusSlaveWaitAI': LuaFunction(
        name='FieldBusSlaveWaitAI', section='3.7.3 Board Card',
        prototype='FieldBusSlaveWaitAI(AIIndex, status, waitMs)',
        arity=3, brief='From Station Mode: Waiting for the Slave AI'),
    'FieldBusSlaveWaitDI': LuaFunction(
        name='FieldBusSlaveWaitDI', section='3.7.3 Board Card',
        prototype='FieldBusSlaveWaitDI(DIIndex, status, waitMs)',
        arity=3, brief='From Station Mode: Waiting for Station DI'),
    'FieldBusSlaveWriteAO': LuaFunction(
        name='FieldBusSlaveWriteAO', section='3.7.3 Board Card',
        prototype='FieldBusSlaveWriteAO(AOStartIndex, writeNum, status)',
        arity=3, brief='From Station Mode Settings: Slave AO'),
    'FieldBusSlaveWriteDO': LuaFunction(
        name='FieldBusSlaveWriteDO', section='3.7.3 Board Card',
        prototype='FieldBusSlaveWriteDO(DOStartIndex, writeNum, status)',
        arity=3, brief='From Station Mode Settings: Slave DO'),
    'GetAI': LuaFunction(
        name='GetAI', section='3.3.2 Analog IO',
        prototype='GetAI(id, thread)',
        arity=2, brief=''),
    'GetAO': LuaFunction(
        name='GetAO', section='3.3.2 Analog IO',
        prototype='GetAO(id, block)',
        arity=2, brief='Read the AO output of the control box'),
    'GetActualTCPNum': LuaFunction(
        name='GetActualTCPNum', section='3.2.14 Trajectory',
        prototype='GetActualTCPNum(flag)',
        arity=1, brief=''),
    'GetActualWObjNum': LuaFunction(
        name='GetActualWObjNum', section='3.2.14 Trajectory',
        prototype='GetActualWObjNum(flag)',
        arity=1, brief=''),
    'GetAuxAI': LuaFunction(
        name='GetAuxAI', section='3.3.6 Expanding IO',
        prototype='GetAuxAI(AINum, thread)',
        arity=2, brief='Get Extended AI value'),
    'GetAuxAO': LuaFunction(
        name='GetAuxAO', section='3.3.6 Expanding IO',
        prototype='GetAuxAO(AONum)',
        arity=1, brief='Get extended AO output'),
    'GetAuxDI': LuaFunction(
        name='GetAuxDI', section='3.3.6 Expanding IO',
        prototype='GetAuxDI(DINum)',
        arity=1, brief='Get Extended DI'),
    'GetAuxDO': LuaFunction(
        name='GetAuxDO', section='3.3.6 Expanding IO',
        prototype='GetAuxDO(DONum)',
        arity=1, brief='Get extended DO output'),
    'GetAxleGenComCycleData': LuaFunction(
        name='GetAxleGenComCycleData', section='3.4.7 End-effector transparent transmission',
        prototype='GetAxleGenComCycleData(enable)',
        arity=1, brief='Get end-cycle data'),
    'GetDI': LuaFunction(
        name='GetDI', section='3.3.1 Digital IO',
        prototype='GetDI(id, thread)',
        arity=2, brief='Block the acquisition of control box digital input'),
    'GetDO': LuaFunction(
        name='GetDO', section='3.3.1 Digital IO',
        prototype='GetDO(id, block)',
        arity=2, brief='Read the DO output of the control box'),
    'GetInverseKinHasSolution': LuaFunction(
        name='GetInverseKinHasSolution', section='3.8.2 Call Function',
        prototype='GetInverseKinHasSolution(type, desc_pos, joint_pos_ref)',
        arity=3, brief=''),
    'GetInverseKinRef': LuaFunction(
        name='GetInverseKinRef', section='3.8.2 Call Function',
        prototype='GetInverseKinRef(type, desc_pos, joint_pos_ref)',
        arity=3, brief='Inverse kinematics solution - specifying position reference'),
    'GetLaserWeldingParamActual': LuaFunction(
        name='GetLaserWeldingParamActual', section='3.5.1 Welding',
        prototype='GetLaserWeldingParamActual(io_type)',
        arity=1, brief='Obtain the actual process parameters of the'),
    'GetLaserWeldingParamTarget': LuaFunction(
        name='GetLaserWeldingParamTarget', section='3.5.1 Welding',
        prototype='GetLaserWeldingParamTarget(io_type)',
        arity=1, brief='Obtain the parameter set for the laser welding'),
    'GetLaserWeldingRunningState': LuaFunction(
        name='GetLaserWeldingRunningState', section='3.5.1 Welding',
        prototype='GetLaserWeldingRunningState(io_type)',
        arity=1, brief='Obtain the operating status of the laser welder'),
    'GetSuckerState': LuaFunction(
        name='GetSuckerState', section='?',
        prototype='',
        arity=None, brief='Get the suction cup status'),
    'GetSysVarvalue': LuaFunction(
        name='GetSysVarvalue', section='3.1.5 Variables',
        prototype='GetSysVarvalue(s_var)',
        arity=1, brief=''),
    'GetToolAI': LuaFunction(
        name='GetToolAI', section='3.3.2 Analog IO',
        prototype='GetToolAI(id, thread)',
        arity=2, brief=''),
    'GetToolAO': LuaFunction(
        name='GetToolAO', section='3.3.2 Analog IO',
        prototype='GetToolAO(id, block)',
        arity=2, brief='Read the AO output of the control box'),
    'GetToolDI': LuaFunction(
        name='GetToolDI', section='3.3.1 Digital IO',
        prototype='GetToolDI(id, thread)',
        arity=2, brief='Block the tool from obtaining numerical input'),
    'GetToolDO': LuaFunction(
        name='GetToolDO', section='3.3.1 Digital IO',
        prototype='GetToolDO(id, block)',
        arity=2, brief='Read the DO output of the control box'),
    'GetTrajectoryPointNum': LuaFunction(
        name='GetTrajectoryPointNum', section='3.2.14 Trajectory',
        prototype='GetTrajectoryPointNum()',
        arity=0, brief=''),
    'GetTrajectoryStartPose': LuaFunction(
        name='GetTrajectoryStartPose', section='3.2.14 Trajectory',
        prototype='GetTrajectoryStartPose(name)',
        arity=1, brief=''),
    'GetVirtualAI': LuaFunction(
        name='GetVirtualAI', section='3.3.3 Virtual IO',
        prototype='GetVirtualAI(id)',
        arity=1, brief=''),
    'GetVirtualDI': LuaFunction(
        name='GetVirtualDI', section='3.3.3 Virtual IO',
        prototype='GetVirtualDI(id)',
        arity=1, brief='Get simulated external DI'),
    'GetVirtualToolAI': LuaFunction(
        name='GetVirtualToolAI', section='3.3.3 Virtual IO',
        prototype='GetVirtualToolAI(id)',
        arity=1, brief=''),
    'GetVirtualToolDI': LuaFunction(
        name='GetVirtualToolDI', section='3.3.3 Virtual IO',
        prototype='GetVirtualToolDI(id)',
        arity=1, brief=''),
    'GetWireSearchOffset': LuaFunction(
        name='GetWireSearchOffset', section='3.5.5 Wire positioning',
        prototype='GetWireSearchOffset(seamType, method, varNameRef, varNameRes)',
        arity=4, brief='Calculate the offset of wire positioning'),
    'HorizonSpiralMotionEnd': LuaFunction(
        name='HorizonSpiralMotionEnd', section='3.2.7 Horizontal Spiral',
        prototype='HorizonSpiralMotionEnd()',
        arity=0, brief=''),
    'HorizonSpiralMotionStart': LuaFunction(
        name='HorizonSpiralMotionStart', section='3.2.7 Horizontal Spiral',
        prototype='HorizonSpiralMotionStart(rad, vel, rot_direction, circle_angle)',
        arity=4, brief=''),
    'ImpedanceControlStartStop': LuaFunction(
        name='ImpedanceControlStartStop', section='3.6.1 Force Control Set',
        prototype='ImpedanceControlStartStop()',
        arity=0, brief='Impedance control start-stop'),
    'LTLaserOff': LuaFunction(
        name='LTLaserOff', section='3.5.3 Laser Tracking',
        prototype='LTLaserOff()',
        arity=0, brief='Turn off the sensor'),
    'LTLaserOn': LuaFunction(
        name='LTLaserOn', section='3.5.3 Laser Tracking',
        prototype='LTLaserOn(Taskid)',
        arity=1, brief=''),
    'LTSearchStart': LuaFunction(
        name='LTSearchStart', section='3.5.3 Laser Tracking',
        prototype='LTSearchStart(refdirection, refdpion, ovl, length, max_time, toolid)',
        arity=6, brief='Start location search'),
    'LTSearchStop': LuaFunction(
        name='LTSearchStop', section='3.5.3 Laser Tracking',
        prototype='LTSearchStop( )',
        arity=0, brief='Stop locating'),
    'LTTrackOff': LuaFunction(
        name='LTTrackOff', section='3.5.3 Laser Tracking',
        prototype='LTTrackOff( )',
        arity=0, brief='Turn off tracking'),
    'LTTrackOn': LuaFunction(
        name='LTTrackOn', section='3.5.3 Laser Tracking',
        prototype='LTTrackOn(toolid)',
        arity=1, brief='Start Tracking'),
    'LaserSensorRecord': LuaFunction(
        name='LaserSensorRecord', section='3.5.3 Laser Tracking',
        prototype='LaserSensorRecord(status , delayMode, delayTime, delayDisExAxisNum, delayDis, sensitivePara, trackMode, triggerMode, runtime, speed)',
        arity=10, brief=''),
    'Lin': LuaFunction(
        name='Lin', section='3.2.2 Straight Line',
        prototype='Lin(point_name, ovl, blendR, search, offset_flag, offset_x, offset_y, offset_z, offset_rx, offset_ry, offset_rz)',
        arity=11, brief='Linear motion'),
    'LoadPosSensorDriver': LuaFunction(
        name='LoadPosSensorDriver', section='3.5.3 Laser Tracking',
        prototype='LoadPosSensorDriver(choiceid)',
        arity=1, brief='Sensor loading'),
    'LoadTPD': LuaFunction(
        name='LoadTPD', section='3.2.11 Trajectory Reproduction',
        prototype='LoadTPD(name)',
        arity=1, brief='Track Preloading'),
    'LoadTrajectory': LuaFunction(
        name='LoadTrajectory', section='3.2.14 Trajectory',
        prototype='LoadTrajectory(name)',
        arity=1, brief='Trajectory Preloading'),
    'LoadTrajectoryJ': LuaFunction(
        name='LoadTrajectoryJ', section='3.2.15 Trajectory J',
        prototype='LoadTrajectoryJ(name, ovl, opt)',
        arity=3, brief=''),
    'MatrixGetCount': LuaFunction(
        name='MatrixGetCount', section='3.7.1 Matrix Movement',
        prototype='MatrixGetCount(name)',
        arity=1, brief='Get the current matrix position index'),
    'MatrixMoveEnd': LuaFunction(
        name='MatrixMoveEnd', section='3.7.1 Matrix Movement',
        prototype='MatrixMoveEnd()',
        arity=0, brief='Matrix movement completed'),
    'MatrixMoveStart': LuaFunction(
        name='MatrixMoveStart', section='3.7.1 Matrix Movement',
        prototype='MatrixMoveStart(name, direction, speed)',
        arity=3, brief='Matrix movement started'),
    'MatrixSetCountPlus': LuaFunction(
        name='MatrixSetCountPlus', section='3.7.1 Matrix Movement',
        prototype='MatrixSetCountPlus(name)',
        arity=1, brief='Matrix Operation Count'),
    'MatrixSetStartCount': LuaFunction(
        name='MatrixSetStartCount', section='3.7.1 Matrix Movement',
        prototype='MatrixSetStartCount(name,curRows,curCols,curLayers,)',
        arity=4, brief='Configuration starting count'),
    'ModbusMasterReadAI': LuaFunction(
        name='ModbusMasterReadAI', section='3.7.1 Modbus',
        prototype='ModbusMasterReadAI(Modbus_name, Register_name, Register_num)',
        arity=3, brief=''),
    'ModbusMasterReadAI_RTU': LuaFunction(
        name='ModbusMasterReadAI_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterReadAI_RTU(Modbus_name, Register_name, Register_num)',
        arity=3, brief=''),
    'ModbusMasterReadAO': LuaFunction(
        name='ModbusMasterReadAO', section='3.7.1 Modbus',
        prototype='ModbusMasterReadAO(Modbus_name, Register_name, Register_num)',
        arity=3, brief=''),
    'ModbusMasterReadAO_RTU': LuaFunction(
        name='ModbusMasterReadAO_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterReadAO_RTU(Modbus_name, Register_name, Register_num)',
        arity=3, brief='Read analog output (read hold register)'),
    'ModbusMasterReadDI': LuaFunction(
        name='ModbusMasterReadDI', section='3.7.1 Modbus',
        prototype='ModbusMasterReadDI(Modbus_name, Register_name, Register_num)',
        arity=3, brief=''),
    'ModbusMasterReadDI_RTU': LuaFunction(
        name='ModbusMasterReadDI_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterReadDI_RTU(Modbus_name, Register_name, Register_num)',
        arity=3, brief=''),
    'ModbusMasterReadDO': LuaFunction(
        name='ModbusMasterReadDO', section='3.7.1 Modbus',
        prototype='ModbusMasterReadDO(Modbus_name, Register_name, Register_num)',
        arity=3, brief=''),
    'ModbusMasterReadDO_RTU': LuaFunction(
        name='ModbusMasterReadDO_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterReadDO_RTU(Modbus_name,Register_name, Register_num)',
        arity=3, brief=''),
    'ModbusMasterReadReg_RTU': LuaFunction(
        name='ModbusMasterReadReg_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterReadReg_RTU(fun_code, reg_add, reg_num, add, isthread)',
        arity=5, brief='Read Register Instruction'),
    'ModbusMasterWaitAI': LuaFunction(
        name='ModbusMasterWaitAI', section='3.7.1 Modbus',
        prototype='ModbusMasterWaitAI(Modbus_name, Register_name, Waiting_state, Register_value, Waiting_time)',
        arity=5, brief=''),
    'ModbusMasterWaitAI_RTU': LuaFunction(
        name='ModbusMasterWaitAI_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterWaitAI_RTU(Modbus_name, Register_name, Waiting_state, Register_value, Waiting_time)',
        arity=5, brief='Waiting for Digital Input Settings (Waiting for'),
    'ModbusMasterWaitDI': LuaFunction(
        name='ModbusMasterWaitDI', section='3.7.1 Modbus',
        prototype='ModbusMasterWaitDI(Modbus_name, Register _name, Waiting_state, Waiting_time)',
        arity=4, brief=''),
    'ModbusMasterWaitDI_RTU': LuaFunction(
        name='ModbusMasterWaitDI_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterWaitDI_RTU(Modbus_name, Register _name, Waiting_state, Waiting_time)',
        arity=4, brief=''),
    'ModbusMasterWriteAO': LuaFunction(
        name='ModbusMasterWriteAO', section='3.7.1 Modbus',
        prototype='ModbusMasterWriteAO(Modbus_name, Register_name, Register_num, {Register_value})',
        arity=4, brief=''),
    'ModbusMasterWriteAO_RTU': LuaFunction(
        name='ModbusMasterWriteAO_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterWriteAO_RTU(Modbus_name, Register_name, Register_num, {Register_value})',
        arity=4, brief=''),
    'ModbusMasterWriteDO': LuaFunction(
        name='ModbusMasterWriteDO', section='3.7.1 Modbus',
        prototype='ModbusMasterWriteDO(Modbus_name, Register_name, Register_num, {Register_value})',
        arity=4, brief=''),
    'ModbusMasterWriteDO_RTU': LuaFunction(
        name='ModbusMasterWriteDO_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterWriteDO_RTU(Modbus_name, Register_name, Register_num, {Register_value})',
        arity=4, brief=''),
    'ModbusMasterWriteReg_RTU': LuaFunction(
        name='ModbusMasterWriteReg_RTU', section='?',
        prototype='',
        arity=None, brief='Write Register'),
    'ModbusMasterWrite_RTU': LuaFunction(
        name='ModbusMasterWrite_RTU', section='3.7.1 Modbus',
        prototype='ModbusMasterWrite_RTU(fun_code, reg_add, reg_num, reg_value, add, isthread)',
        arity=6, brief=''),
    'ModbusRegGetData': LuaFunction(
        name='ModbusRegGetData', section='3.7.1 Modbus',
        prototype='ModbusRegGetData(reg_num,isthread)',
        arity=2, brief=''),
    'ModbusRegRead': LuaFunction(
        name='ModbusRegRead', section='3.7.1 Modbus',
        prototype='ModbusRegRead(fun_code, reg_add, reg_num, add, isthread)',
        arity=5, brief=''),
    'ModbusRegWrite': LuaFunction(
        name='ModbusRegWrite', section='3.7.1 Modbus',
        prototype='ModbusRegWrite(fun_code, reg_add, reg_num, reg_value, add, isthread)',
        arity=6, brief=''),
    'ModbusSlaveReadDI_RTU': LuaFunction(
        name='ModbusSlaveReadDI_RTU', section='?',
        prototype='',
        arity=None, brief='Read digital input (read coil)'),
    'ModbusSlaveReadDO': LuaFunction(
        name='ModbusSlaveReadDO', section='3.7.1 Modbus',
        prototype='ModbusSlaveReadDO(Register_name, Register_num)',
        arity=2, brief=''),
    'ModbusSlaveReadDO_RTU': LuaFunction(
        name='ModbusSlaveReadDO_RTU', section='3.7.1 Modbus',
        prototype='ModbusSlaveReadDO_RTU(Register_name, Register_num)',
        arity=2, brief=''),
    'ModbusSlaveWaitAI': LuaFunction(
        name='ModbusSlaveWaitAI', section='3.7.1 Modbus',
        prototype='ModbusSlaveWaitAI(Register_name, Waiting_state,Register_value , Waiting_time)',
        arity=4, brief=''),
    'ModbusSlaveWaitAI_RTU': LuaFunction(
        name='ModbusSlaveWaitAI_RTU', section='3.7.1 Modbus',
        prototype='ModbusSlaveWaitAI_RTU(Register_name, Waiting_state,Register_value, Waiting_time)',
        arity=4, brief=''),
    'ModbusSlaveWaitDI': LuaFunction(
        name='ModbusSlaveWaitDI', section='3.7.1 Modbus',
        prototype='ModbusSlaveWaitDI(Register_name, Waiting_state, Waiting_time)',
        arity=3, brief=''),
    'ModbusSlaveWaitDI_RTU': LuaFunction(
        name='ModbusSlaveWaitDI_RTU', section='3.7.1 Modbus',
        prototype='ModbusSlaveWaitDI_RTU(Register_name, Waiting_state, Waiting_time)',
        arity=3, brief=''),
    'ModbusSlaveWriteDO': LuaFunction(
        name='ModbusSlaveWriteDO', section='3.7.1 Modbus',
        prototype='ModbusSlaveWriteDO(Register_name, Register_num, {Register_value})',
        arity=3, brief=''),
    'ModbusSlaveWriteDO_RTU': LuaFunction(
        name='ModbusSlaveWriteDO_RTU', section='3.7.1 Modbus',
        prototype='ModbusSlaveWriteDO_RTU(Register_name, Register_num, {Register_value})',
        arity=3, brief='Slave Digital Output Settings (Write Discrete'),
    'MoveAOStart': LuaFunction(
        name='MoveAOStart', section='3.3.5 Exercise AO',
        prototype='MoveAOStart(AONum, maxTCPSpeed, maxAOPercent, zeroZoneCmp)',
        arity=4, brief=''),
    'MoveAOStop': LuaFunction(
        name='MoveAOStop', section='3.3.5 Exercise AO',
        prototype='MoveAOStop()',
        arity=0, brief='Control box motion AO ends'),
    'MoveC': LuaFunction(
        name='MoveC', section='3.2.3 Arc',
        prototype='MoveC(pj1, pj2, pj3, pj4, pj5, pj6, px, py, pz, prx, pry, prz, ptool, puser, pspeed, pacc, pep1, pep2, pep3, pep4, poffset, offset_px, offset_py, offset_pz, offset_prx, offset_pry, offset_prz, tj1, tj2, tj3, tj4, tj5, tj6, tx, ty, tz, trx, try, trz, ttool, tuser, tspeed, tacc, tep1, tep2, tep3, tep4, toffset, offset_tx, offset_ty, offset_tz, offset_trx, offset_try, offset_trz, ovl, blendR)',
        arity=56, brief='Cartesian space circular motion'),
    'MoveCart': LuaFunction(
        name='MoveCart', section='3.2.14 Trajectory',
        prototype='MoveCart(desc_pos, ool, user, vel, acc, ovl, blendT, config)',
        arity=8, brief='Cartesian Space Point to Point Motion'),
    'MoveDOStart': LuaFunction(
        name='MoveDOStart', section='3.3.4 Sports DO',
        prototype='MoveDOStart(doNum, distance, dutyCycle)',
        arity=3, brief='Parallel setting of control box DO status starts during movement'),
    'MoveDOStop': LuaFunction(
        name='MoveDOStop', section='3.3.4 Sports DO',
        prototype='MoveDOStop()',
        arity=0, brief='Parallel setting of control box DO status to stop during movement'),
    'MoveGripper': LuaFunction(
        name='MoveGripper', section='3.4.1 Gripper',
        prototype='MoveGripper(index, pos, vel, force, max_time, block)',
        arity=6, brief=''),
    'MoveIntersectLine': LuaFunction(
        name='MoveIntersectLine', section='3.2.19 line of intersection',
        prototype='MoveIntersectLine(point1,point2,point3,point4,point5,point6, pieceP1,piece P2,pieceP3,pieceP4,pieceP5,pieceP6,extAxisFlag,extP1,extP2,extP3,extP4,sp eed,acc,moveDirection,offsetPos[6])',
        arity=21, brief='Intersection line motion'),
    'MoveJ': LuaFunction(
        name='MoveJ', section='3.2.1 Point to point',
        prototype='MoveJ(j1, j2, j3, j4, j5, j6, x, y, z, rx, ry, rz, tool, user, speed, acc, ovl, ep1, ep2, ep3, ep4, blendT, offset, offset_x, offset_y, offset_z, offset_rx, offset_ry, offset_rz)',
        arity=29, brief='Joint Space Motion'),
    'MoveL': LuaFunction(
        name='MoveL', section='3.2.2 Straight Line',
        prototype='MoveL(j1, j2, j3, j4, j5, j6, x, y, z, rx, ry, rz, tool, user, speed, acc, ovl, blendR, blendRMode, ep1, ep2, ep3, ep4, search, offset, offset_x, offset_y, offset_z, offset_rx, offset_ry, offset_rz, oacc, velAccParamMode)',
        arity=33, brief='Cartesian Space Linear Motion'),
    'MoveLTR': LuaFunction(
        name='MoveLTR', section='3.5.3 Laser Tracking',
        prototype='MoveLTR( )',
        arity=0, brief='Laser Tracking Reproduction'),
    'MoveStationary': LuaFunction(
        name='MoveStationary', section='3.5.3 Laser Tracking',
        prototype='MoveStationary( )',
        arity=0, brief='In-situ planning interpolation and filling instructions'),
    'MoveTPD': LuaFunction(
        name='MoveTPD', section='3.2.11 Trajectory Reproduction',
        prototype='MoveTPD(name, blend, ovl)',
        arity=3, brief=''),
    'MoveToIntersectLineStart': LuaFunction(
        name='MoveToIntersectLineStart', section='3.2.19 line of intersection',
        prototype='MoveToIntersectLineStart(mainP1,mainP2,mainP3,mainP4,mainP5,mainP6,p ieceP1,pieceP2,pieceP3,pieceP4,pieceP5,pieceP6,extAxisFlag,extP1,speed,ac c,moveType,moveDirection,offsetPos[6])',
        arity=19, brief=''),
    'MoveToLaserRecordEnd': LuaFunction(
        name='MoveToLaserRecordEnd', section='3.5.4 Laser Recording',
        prototype='MoveToLaserRecordEnd( )',
        arity=0, brief='Move to the end point of the weld seam'),
    'MoveToLaserRecordStart': LuaFunction(
        name='MoveToLaserRecordStart', section='3.5.4 Laser Recording',
        prototype='MoveToLaserRecordStart( )',
        arity=0, brief='Move to the starting point of the weld seam'),
    'MoveToTPDStart': LuaFunction(
        name='MoveToTPDStart', section='3.2.11 Trajectory Reproduction',
        prototype='MoveToTPDStart(name, moveType, ovl)',
        arity=3, brief='Movement to the start of the TPD trajectory recording'),
    'MoveToolAOStart': LuaFunction(
        name='MoveToolAOStart', section='3.3.5 Exercise AO',
        prototype='MoveToolAOStart(AONum, maxTCPSpeed, maxAOPercent, zeroZoneCmp)',
        arity=4, brief='Tool motion AO starts'),
    'MoveToolAOStop': LuaFunction(
        name='MoveToolAOStop', section='3.3.5 Exercise AO',
        prototype='MoveToolAOStop()',
        arity=0, brief='Tool motion AO ends'),
    'MoveToolDOStart': LuaFunction(
        name='MoveToolDOStart', section='3.3.4 Sports DO',
        prototype='MoveToolDOStart(doNum, distance, dutyCycle)',
        arity=3, brief='Parallel setting of tool DO status during motion begins'),
    'MoveToolDOStop': LuaFunction(
        name='MoveToolDOStop', section='3.3.4 Sports DO',
        prototype='MoveToolDOStop()',
        arity=0, brief='Set tool DO status to stop in parallel during motion'),
    'MoveTrajectory': LuaFunction(
        name='MoveTrajectory', section='3.2.14 Trajectory',
        prototype='MoveTrajectory(name, ovl)',
        arity=2, brief='Trajectory Reproduction'),
    'MoveTrajectoryJ': LuaFunction(
        name='MoveTrajectoryJ', section='3.2.15 Trajectory J',
        prototype='MoveTrajectoryJ( )',
        arity=0, brief=''),
    'MultilayerOffsetTrsfToBase': LuaFunction(
        name='MultilayerOffsetTrsfToBase', section='3.5.2 Arc Tracking',
        prototype='MultilayerOffsetTrsfToBase(pointO.x, pointO.y, pointO.z, pointX.x, pointX.y, pointX.z, pointZ.x, pointZ.y, pointZ.z, dx, dy, dry)',
        arity=12, brief=''),
    'MultiplayerOffsetTrsfToBase': LuaFunction(
        name='MultiplayerOffsetTrsfToBase', section='?',
        prototype='',
        arity=None, brief='Offset coordinate variation - multi-layer and'),
    'NewAuxThread': LuaFunction(
        name='NewAuxThread', section='3.8.1 Auxiliary Threads',
        prototype='NewAuxThread(func_name, func_ Para)',
        arity=2, brief='Creating auxiliary threads'),
    'NewDofile': LuaFunction(
        name='NewDofile', section='3.1.4 Subroutines',
        prototype='NewDofile(name_path, layer, id)',
        arity=3, brief='subroutine call'),
    'NewSP': LuaFunction(
        name='NewSP', section='3.2.9 New spline',
        prototype='NewSP(point_name, ovl, blendR, islast_point)',
        arity=4, brief='Method 1: New spline multi-point trajectory segment'),
    'NewSpiral': LuaFunction(
        name='NewSpiral', section='3.2.6 New Spiral',
        prototype='NewSpiral(desc_pos_name, ovl, offset_flag = 2, offset_x, offset_y, offset_z, offset_rx, offset_ry, offset_rz, circle_num, circle_angle, rad_init, rad_add , rot_direction, velAccParamMode, speed, acc)',
        arity=17, brief='New Spiral Motion'),
    'NewSplineEnd': LuaFunction(
        name='NewSplineEnd', section='3.2.9 New spline',
        prototype='NewSplineEnd()',
        arity=0, brief='End of spline group'),
    'NewSplinePoint': LuaFunction(
        name='NewSplinePoint', section='3.2.9 New spline',
        prototype='NewSplinePoint(j1, j2, j3, j4, j5, j6, x, y, z, rx, ry, rz, tool, user, speed, acc, ovl , blendR )',
        arity=18, brief=''),
    'NewSplineStart': LuaFunction(
        name='NewSplineStart', section='3.2.9 New spline',
        prototype='NewSplineStart(Con_mode, Gac_time)',
        arity=2, brief='New spline multi-point trajectory start'),
    'OriginPointWeaveEnd': LuaFunction(
        name='OriginPointWeaveEnd', section='3.2.10 Swing',
        prototype='OriginPointWeaveEnd()',
        arity=0, brief='The fixed-point swing ends'),
    'OriginPointWeaveStart': LuaFunction(
        name='OriginPointWeaveStart', section='3.2.10 Swing',
        prototype='OriginPointWeaveStart(weaveNum,mode,refPoint,weaveTime)',
        arity=4, brief='The fixed-point swing begins'),
    'PTP': LuaFunction(
        name='PTP', section='3.2.1 Point to point',
        prototype='PTP(point_name, ovl, blendT, offset_flag, offset_x, offset_y, offset_z, offset_rx, offset_ry, offset_rz)',
        arity=10, brief='point-to-point'),
    'Pause': LuaFunction(
        name='Pause', section='3.1.3 Pause',
        prototype='Pause(num)',
        arity=1, brief='Pause'),
    'PointTableSwitch': LuaFunction(
        name='PointTableSwitch', section='3.8.3 Point Table',
        prototype='PointTableSwitch(point_table_name)',
        arity=1, brief='Point Switching'),
    'PointTableUpdateLua': LuaFunction(
        name='PointTableUpdateLua', section='3.8.3 Point Table',
        prototype='PointTableUpdateLua(lua_name)',
        arity=1, brief=''),
    'PointsOffsetDisable': LuaFunction(
        name='PointsOffsetDisable', section='3.2.12 point offset',
        prototype='PointsOffsetDisable()',
        arity=0, brief='End of overall point offset'),
    'PointsOffsetEnable': LuaFunction(
        name='PointsOffsetEnable', section='3.2.12 point offset',
        prototype='PointsOffsetEnable(flag, x,y,z,rx,ry,rz)',
        arity=7, brief=''),
    'PolishingClearError': LuaFunction(
        name='PolishingClearError', section='3.4.5 Grinding equipment',
        prototype='PolishingClearError()',
        arity=0, brief='Error Clearing'),
    'PolishingDeviceEnable': LuaFunction(
        name='PolishingDeviceEnable', section='3.4.5 Grinding equipment',
        prototype='PolishingDeviceEnable(status)',
        arity=1, brief='Device Enable Settings'),
    'PolishingLoadComDriver': LuaFunction(
        name='PolishingLoadComDriver', section='3.4.5 Grinding equipment',
        prototype='PolishingLoadComDriver()',
        arity=0, brief='Load the polishing head communication driver'),
    'PolishingSetTargetPosition': LuaFunction(
        name='PolishingSetTargetPosition', section='3.4.5 Grinding equipment',
        prototype='PolishingSetTargetPosition(distance)',
        arity=1, brief=''),
    'PolishingSetTargetTorque': LuaFunction(
        name='PolishingSetTargetTorque', section='3.4.5 Grinding equipment',
        prototype='PolishingSetTargetTorque(setN)',
        arity=1, brief=''),
    'PolishingSetTargetTouchForce': LuaFunction(
        name='PolishingSetTargetTouchForce', section='3.4.5 Grinding equipment',
        prototype='PolishingSetTargetTouchForce(conN)',
        arity=1, brief=''),
    'PolishingSetTargetTouchForceTime': LuaFunction(
        name='PolishingSetTargetTouchForceTime', section='3.4.5 Grinding equipment',
        prototype='PolishingSetTargetTouchForceTime(settime)',
        arity=1, brief=''),
    'PolishingSetTargetVelocity': LuaFunction(
        name='PolishingSetTargetVelocity', section='3.4.5 Grinding equipment',
        prototype='PolishingSetTargetVelocity(rot)',
        arity=1, brief=''),
    'PolishingSetWorkPieceWeight': LuaFunction(
        name='PolishingSetWorkPieceWeight', section='3.4.5 Grinding equipment',
        prototype='PolishingSetWorkPieceWeight(weight)',
        arity=1, brief='workpiece weight setting'),
    'PolishingTorqueSensorReset': LuaFunction(
        name='PolishingTorqueSensorReset', section='3.4.5 Grinding equipment',
        prototype='PolishingTorqueSensorReset()',
        arity=0, brief=''),
    'PolishingUnloadComDriver': LuaFunction(
        name='PolishingUnloadComDriver', section='3.4.5 Grinding equipment',
        prototype='PolishingUnloadComDriver()',
        arity=0, brief=''),
    'PostureAdjustOff': LuaFunction(
        name='PostureAdjustOff', section='3.5.6 Attitude Adjustment',
        prototype='PostureAdjustOff( )',
        arity=0, brief='Turn off posture adjustment'),
    'PostureAdjustOn': LuaFunction(
        name='PostureAdjustOn', section='3.5.6 Attitude Adjustment',
        prototype='PostureAdjustOn(plate_type, direction_type={PosA, PosB, PosC}, time, paDisatance_1, inflection_type, paDisatance_2, paDisatance_3 , paDisatance_4, paDisatance_5)',
        arity=11, brief='Enable posture adjustment'),
    'PowerCleanStart': LuaFunction(
        name='PowerCleanStart', section='3.4.2 Spray gun',
        prototype='PowerCleanStart()',
        arity=0, brief='Start cleaning the gun'),
    'PowerCleanStop': LuaFunction(
        name='PowerCleanStop', section='3.4.2 Spray gun',
        prototype='PowerCleanStop()',
        arity=0, brief='Gun cleaning stops'),
    'PrintMsg': LuaFunction(
        name='PrintMsg', section='3.1.5 Variables',
        prototype='PrintMsg(args)',
        arity=1, brief='Print Content'),
    'RegisterVar': LuaFunction(
        name='RegisterVar', section='3.1.5 Variables',
        prototype='RegisterVar(type, var)',
        arity=2, brief='Variable type query'),
    'ResetLaserWeldingErr': LuaFunction(
        name='ResetLaserWeldingErr', section='3.5.1 Welding',
        prototype='ResetLaserWeldingErr(io_type, status)',
        arity=2, brief='Laser welding machine fault reset'),
    'SCIRC': LuaFunction(
        name='SCIRC', section='3.2.8 Spline',
        prototype='SCIRC(pos_p_name, pos_t_name, ovl)',
        arity=3, brief=''),
    'SLIN': LuaFunction(
        name='SLIN', section='3.2.8 Spline',
        prototype='SLIN(point_name, ovl )',
        arity=2, brief=''),
    'SPLCGetAI': LuaFunction(
        name='SPLCGetAI', section='3.3.2 Analog IO',
        prototype='SPLCGetAI(id, condition, value, stime)',
        arity=4, brief=''),
    'SPLCGetDI': LuaFunction(
        name='SPLCGetDI', section='3.3.1 Digital IO',
        prototype='SPLCGetDI(id, status, stime)',
        arity=3, brief='Non blocking access to IO'),
    'SPLCGetToolAI': LuaFunction(
        name='SPLCGetToolAI', section='3.3.2 Analog IO',
        prototype='SPLCGetToolAI(id, condition, value, stime)',
        arity=4, brief='Non blocking acquisition of control box analog input'),
    'SPLCGetToolDI': LuaFunction(
        name='SPLCGetToolDI', section='3.3.1 Digital IO',
        prototype='SPLCGetToolDI(id, status, stime)',
        arity=3, brief=''),
    'SPLCSetAO': LuaFunction(
        name='SPLCSetAO', section='3.3.2 Analog IO',
        prototype='SPLCSetAO(id, value)',
        arity=2, brief='Set control box analog non blocking output'),
    'SPLCSetDO': LuaFunction(
        name='SPLCSetDO', section='3.3.1 Digital IO',
        prototype='SPLCSetDO(id, status)',
        arity=2, brief='Set control box digital quantity non blocking output'),
    'SPLCSetToolAO': LuaFunction(
        name='SPLCSetToolAO', section='3.3.2 Analog IO',
        prototype='SPLCSetToolAO(id, value)',
        arity=2, brief='Set control box analog non blocking output'),
    'SPLSetToolDO': LuaFunction(
        name='SPLSetToolDO', section='3.3.1 Digital IO',
        prototype='SPLSetToolDO(id, status, smooth, thread)',
        arity=4, brief='Set tool digital quantity non blocking output'),
    'SPTP': LuaFunction(
        name='SPTP', section='3.2.8 Spline',
        prototype='SPTP(point_name, ovl)',
        arity=2, brief=''),
    'ServoCart': LuaFunction(
        name='ServoCart', section='3.2.13 Servo',
        prototype='ServoCart(mode, x, y, z, Rx, Ry, Rz, pos_gainx, pos_gainy, pos_gainz, pos_gainrx, pos_gainry, pos_gainrz, exaxis_pos, acc, vel, cmdT, filterT, gain)',
        arity=19, brief='Cartesian Space Servo mode Motion'),
    'ServoJ': LuaFunction(
        name='ServoJ', section='3.2.13 Servo',
        prototype='ServoJ(j1,j2,j3,j4,j5,j6,ep1,ep2,ep3,ep4,acc,vel,interval,filterTime,posGaim)',
        arity=15, brief='Joint-space servo motion'),
    'ServoMoveEnd': LuaFunction(
        name='ServoMoveEnd', section='3.2.13 Servo',
        prototype='ServoMoveEnd()',
        arity=0, brief='End of servo motion'),
    'ServoMoveStart': LuaFunction(
        name='ServoMoveStart', section='3.2.13 Servo',
        prototype='ServoMoveStart()',
        arity=0, brief='Servo motion begins'),
    'SetAO': LuaFunction(
        name='SetAO', section='3.3.2 Analog IO',
        prototype='SetAO(id, value, thread)',
        arity=3, brief='Set control box analog blocking output'),
    'SetAnticollision': LuaFunction(
        name='SetAnticollision', section='3.3.9 Collision Level',
        prototype='SetAnticollision(mode, level, config)',
        arity=3, brief='Collision Level Setting'),
    'SetAspirated': LuaFunction(
        name='SetAspirated', section='3.5.1 Welding',
        prototype='SetAspirated(ioType, airControl)',
        arity=2, brief='Air supply'),
    'SetAuxAO': LuaFunction(
        name='SetAuxAO', section='3.3.6 Expanding IO',
        prototype='SetAuxAO(AONum, value, thread)',
        arity=3, brief='Set Extended AO'),
    'SetAuxDO': LuaFunction(
        name='SetAuxDO', section='3.3.6 Expanding IO',
        prototype='SetAuxDO(DONum, status, smooth, thread)',
        arity=4, brief='Set Extended DO'),
    'SetDFCForce': LuaFunction(
        name='SetDFCForce', section='3.4.5 Grinding equipment',
        prototype='SetDFCForce(channel,force)',
        arity=2, brief='DFC grinding head settings'),
    'SetDO': LuaFunction(
        name='SetDO', section='3.3.1 Digital IO',
        prototype='SetDO(id, status, smooth, thread)',
        arity=4, brief='Set the digital quantity blocking output of the control box'),
    'SetDexterousHandsAct': LuaFunction(
        name='SetDexterousHandsAct', section='3.4.8 Dexterous Hand',
        prototype='SetDexterousHandsAct(len)',
        arity=1, brief='Redeploy and activate the dexterous hand'),
    'SetDexterousHandsMove': LuaFunction(
        name='SetDexterousHandsMove', section='3.4.8 Dexterous Hand',
        prototype='SetDexterousHandsMove(idStart,slaveNum,pos[16],force[16],maxTime)',
        arity=5, brief='Dexterous Hand Movement'),
    'SetEversewireFeed': LuaFunction(
        name='SetEversewireFeed', section='?',
        prototype='',
        arity=None, brief='Reverse wire feeding'),
    'SetForwardWireFeed': LuaFunction(
        name='SetForwardWireFeed', section='3.5.1 Welding',
        prototype='SetForwardWireFeed(ioType, wireFeed)',
        arity=2, brief='Forward Wire Feed'),
    'SetLaserWeldingEnable': LuaFunction(
        name='SetLaserWeldingEnable', section='?',
        prototype='',
        arity=None, brief='Laser welding machines make it possible to...'),
    'SetLaserWeldingEnableExtDoNum': LuaFunction(
        name='SetLaserWeldingEnableExtDoNum', section='3.5.1 Welding',
        prototype='SetLaserWeldingEnableExtDoNum(DONum)',
        arity=1, brief='The laser welding machine is equipped with'),
    'SetLaserWeldingErrStateExtDiNum': LuaFunction(
        name='SetLaserWeldingErrStateExtDiNum', section='3.5.1 Welding',
        prototype='SetLaserWeldingErrStateExtDiNum(DINum)',
        arity=1, brief='Configuration of the Fault Status'),
    'SetLaserWeldingParam': LuaFunction(
        name='SetLaserWeldingParam', section='3.5.1 Welding',
        prototype='SetLaserWeldingParam(io_type,Num,scanSpeed,scanWidth,peakPower,duty Cycle,Freq)',
        arity=7, brief='Configure the parameters of the laser welding machine'),
    'SetLaserWeldingRunningStateExtDiNum': LuaFunction(
        name='SetLaserWeldingRunningStateExtDiNum', section='3.5.1 Welding',
        prototype='SetLaserWeldingRunningStateExtDiNum(DINum)',
        arity=1, brief='Configure the operating status (light'),
    'SetLaserWeldingStart': LuaFunction(
        name='SetLaserWeldingStart', section='3.5.1 Welding',
        prototype='SetLaserWeldingStart(io_type, status, max_waittime)',
        arity=3, brief='Turn the laser welder on and off'),
    'SetLaserWeldingStartExtDoNum': LuaFunction(
        name='SetLaserWeldingStartExtDoNum', section='3.5.1 Welding',
        prototype='SetLaserWeldingStartExtDoNum(DONum)',
        arity=1, brief='The laser welding machine is equipped with'),
    'SetOaccScale': LuaFunction(
        name='SetOaccScale', section='3.3.11 Acceleration',
        prototype='SetOaccScale(acc)',
        arity=1, brief=''),
    'SetPointToDatabase': LuaFunction(
        name='SetPointToDatabase', section='3.5.5 Wire positioning',
        prototype='SetPointToDatabase(varName, pos)',
        arity=2, brief=''),
    'SetReverseWireFeed': LuaFunction(
        name='SetReverseWireFeed', section='3.5.1 Welding',
        prototype='SetReverseWireFeed(ioType, wireFeed)',
        arity=2, brief=''),
    'SetStationTrackPara': LuaFunction(
        name='SetStationTrackPara', section='3.4.4 Conveyor Belt',
        prototype='SetStationTrackPara()',
        arity=0, brief='Conveyor Belt Fixed Point Tracking Parameter Settings'),
    'SetSuckerCtrl': LuaFunction(
        name='SetSuckerCtrl', section='3.4.6 Suction cups',
        prototype='SetSuckerCtrl(slaveID, len, ctrlValue)',
        arity=3, brief='Suction cup control instructions'),
    'SetSysVarvalue': LuaFunction(
        name='SetSysVarvalue', section='3.1.5 Variables',
        prototype='SetSysVarvalue(s_var, value)',
        arity=2, brief=''),
    'SetToolAO': LuaFunction(
        name='SetToolAO', section='3.3.2 Analog IO',
        prototype='SetToolAO(id, value, thread)',
        arity=3, brief=''),
    'SetToolDO': LuaFunction(
        name='SetToolDO', section='3.3.1 Digital IO',
        prototype='SetToolDO(id, status, smooth, thread)',
        arity=4, brief='Set tool digital quantity to block output'),
    'SetToolList': LuaFunction(
        name='SetToolList', section='3.2.18 Tool Conversion',
        prototype='SetToolList(name)',
        arity=1, brief='Set tool coordinate system'),
    'SetVirtualAI': LuaFunction(
        name='SetVirtualAI', section='3.3.3 Virtual IO',
        prototype='SetVirtualAI(id, value)',
        arity=2, brief='Set up simulated external AI'),
    'SetVirtualDI': LuaFunction(
        name='SetVirtualDI', section='3.3.3 Virtual IO',
        prototype='SetVirtualDI(id, status)',
        arity=2, brief='Set up simulated external DI'),
    'SetVirtualToolAI': LuaFunction(
        name='SetVirtualToolAI', section='3.3.3 Virtual IO',
        prototype='SetVirtualToolAI(id, value)',
        arity=2, brief='Set up simulated external AI'),
    'SetVirtualToolDI': LuaFunction(
        name='SetVirtualToolDI', section='3.3.3 Virtual IO',
        prototype='SetVirtualToolDI(id, status)',
        arity=2, brief='Set simulated external tool DI'),
    'SetWObjList': LuaFunction(
        name='SetWObjList', section='3.3.7 Coordinate System',
        prototype='SetWObjList(name)',
        arity=1, brief='Set the workpiece coordinate series table'),
    'SndRcvAxleGenComCmdData': LuaFunction(
        name='SndRcvAxleGenComCmdData', section='3.4.7 End-effector transparent transmission',
        prototype='SndRcvAxleGenComCmdData(len)',
        arity=1, brief='Send end-effector acyclic data'),
    'Spiral': LuaFunction(
        name='Spiral', section='3.2.5 Spiral',
        prototype='Spiral(pos_1_name, pos_2_name, pos_3_name, ovl, offset_flag, offset_x, offset_y, offset_z, offset_rx, offset_ry, offset_rz, circle_num, circle_angle_Co_rx, circle_angle_Co_ry, circle_angle_Co_rz, rad_add, rotaxis_add)',
        arity=17, brief='Spiral motion'),
    'SplineCIRC': LuaFunction(
        name='SplineCIRC', section='3.2.8 Spline',
        prototype='SplineCIRC(pj1, pj2, pj3, pj4, pj5, pj6, px, py, pz, prx, pry, prz, ptool, puser, pspeed, pacc, tj1, tj2, tj3, tj4, tj5, tj6, tx, ty, tz, trx, try, trz, ttool, tuser, tspeed, tacc,ovl)',
        arity=33, brief=''),
    'SplineEnd': LuaFunction(
        name='SplineEnd', section='3.2.8 Spline',
        prototype='SplineEnd()',
        arity=0, brief='End of spline group'),
    'SplineLINE': LuaFunction(
        name='SplineLINE', section='3.2.8 Spline',
        prototype='SplineLINE(j1, j2, j3, j4, j5, j6, x, y, z, rx, ry, rz, tool, user, speed, acc, ovl)',
        arity=17, brief=''),
    'SplinePTP': LuaFunction(
        name='SplinePTP', section='3.2.8 Spline',
        prototype='SplinePTP(j1, j2, j3, j4, j5, j6, x, y, z, rx, ry, rz, tool, user, speed, acc, ovl)',
        arity=17, brief=''),
    'SplineStart': LuaFunction(
        name='SplineStart', section='3.2.8 Spline',
        prototype='SplineStart()',
        arity=0, brief='Spline motion begins'),
    'SprayStart': LuaFunction(
        name='SprayStart', section='3.4.2 Spray gun',
        prototype='SprayStart()',
        arity=0, brief='Spraying begins'),
    'SprayStop': LuaFunction(
        name='SprayStop', section='3.4.2 Spray gun',
        prototype='SprayStop()',
        arity=0, brief='Stop spraying'),
    'ToolTrsfEnd': LuaFunction(
        name='ToolTrsfEnd', section='3.2.18 Tool Conversion',
        prototype='ToolTrsfEnd()',
        arity=0, brief='Tool coordinate system conversion completed'),
    'ToolTrsfStart': LuaFunction(
        name='ToolTrsfStart', section='3.2.18 Tool Conversion',
        prototype='ToolTrsfStart(id)',
        arity=1, brief=''),
    'TorqueRecordEnd': LuaFunction(
        name='TorqueRecordEnd', section='3.6.2 Torque Recording',
        prototype='TorqueRecordEnd( )',
        arity=0, brief='Torque recording stops'),
    'TorqueRecordReset': LuaFunction(
        name='TorqueRecordReset', section='3.6.2 Torque Recording',
        prototype='TorqueRecordReset( )',
        arity=0, brief=''),
    'TorqueRecordStart': LuaFunction(
        name='TorqueRecordStart', section='3.6.2 Torque Recording',
        prototype='TorqueRecordStart(flag, negativevalues, positivevalues, collisionTime)',
        arity=4, brief='Torque recording begins'),
    'UnloadPosSensorDriver': LuaFunction(
        name='UnloadPosSensorDriver', section='3.5.3 Laser Tracking',
        prototype='UnloadPosSensorDriver(choiceid)',
        arity=1, brief='Sensor Unloading'),
    'WaitAI': LuaFunction(
        name='WaitAI', section='3.1.2 Waiting',
        prototype='WaitAI(id, sign, value, maxtime, opt)',
        arity=5, brief='Waiting for analog input from the control box'),
    'WaitAuxAI': LuaFunction(
        name='WaitAuxAI', section='3.3.6 Expanding IO',
        prototype='WaitAuxAI(AINum, sign, value, time, timeout)',
        arity=5, brief='Waiting for extended AI input'),
    'WaitAuxDI': LuaFunction(
        name='WaitAuxDI', section='3.3.6 Expanding IO',
        prototype='WaitAuxDI(DINum, bOpen,time, timeout)',
        arity=4, brief='Waiting for extended DI input'),
    'WaitDI': LuaFunction(
        name='WaitDI', section='3.1.2 Waiting',
        prototype='WaitDI(id, status,maxtime, opt)',
        arity=4, brief='Waiting for digital input from the control box'),
    'WaitMs': LuaFunction(
        name='WaitMs', section='3.1.2 Waiting',
        prototype='WaitMs(t_ms)',
        arity=1, brief='Wait for a specified time'),
    'WaitMultiDI': LuaFunction(
        name='WaitMultiDI', section='3.1.2 Waiting',
        prototype='WaitMultiDI(mode, id, status,maxtime, opt)',
        arity=5, brief='Waiting for multiple digital inputs from the control box'),
    'WaitStationaryMotionDone': LuaFunction(
        name='WaitStationaryMotionDone', section='3.4.4 Conveyor Belt',
        prototype='WaitStationaryMotionDone()',
        arity=0, brief='Wait for the in-place empty movement to complete'),
    'WaitSuckerState': LuaFunction(
        name='WaitSuckerState', section='3.4.6 Suction cups',
        prototype='WaitSuckerState(slaveID, state, ms)',
        arity=3, brief='Wait for the suction cup to be adsorbed'),
    'WaitToolAI': LuaFunction(
        name='WaitToolAI', section='3.1.2 Waiting',
        prototype='WaitToolAI(id, sign, value, maxtime, opt)',
        arity=5, brief='Waiting for tool analog input'),
    'WaitToolDI': LuaFunction(
        name='WaitToolDI', section='3.1.2 Waiting',
        prototype='WaitToolDI(id, status,maxtime, opt)',
        arity=4, brief='Waiting for tool numerical input'),
    'WeaveChangeEnd': LuaFunction(
        name='WeaveChangeEnd', section='3.5.1 Welding',
        prototype='WeaveChangeEnd( )',
        arity=0, brief='End of weaving gradient.'),
    'WeaveChangeStart': LuaFunction(
        name='WeaveChangeStart', section='3.5.1 Welding',
        prototype='WeaveChangeStart(weaveChangeFlag, weaveChangeNum, velStart, velEnd)',
        arity=4, brief='Start of weaving gradient.'),
    'WeaveEnd': LuaFunction(
        name='WeaveEnd', section='3.2.10 Swing',
        prototype='WeaveEnd(weaveNum)',
        arity=1, brief='End of swing'),
    'WeaveEndSim': LuaFunction(
        name='WeaveEndSim', section='3.2.10 Swing',
        prototype='WeaveEndSim(weaveNum)',
        arity=1, brief='Simulation swing ends'),
    'WeaveInspectEnd': LuaFunction(
        name='WeaveInspectEnd', section='3.2.10 Swing',
        prototype='WeaveInspectEnd(weaveNum)',
        arity=1, brief='Stop trajectory warning'),
    'WeaveInspectStart': LuaFunction(
        name='WeaveInspectStart', section='3.2.10 Swing',
        prototype='WeaveInspectStart(weaveNum)',
        arity=1, brief='Start trajectory warning'),
    'WeaveStart': LuaFunction(
        name='WeaveStart', section='3.2.10 Swing',
        prototype='WeaveStart(weaveNum)',
        arity=1, brief='Swing begins'),
    'WeaveStartSim': LuaFunction(
        name='WeaveStartSim', section='3.2.10 Swing',
        prototype='WeaveStartSim(weaveNum)',
        arity=1, brief='Simulation swing begins'),
    'WeldingGetCurrentRelation': LuaFunction(
        name='WeldingGetCurrentRelation', section='3.5.1 Welding',
        prototype='WeldingGetCurrentRelation()',
        arity=0, brief='Get the relationship between welding current and'),
    'WeldingGetVoltageRelation': LuaFunction(
        name='WeldingGetVoltageRelation', section='3.5.1 Welding',
        prototype='WeldingGetVoltageRelation()',
        arity=0, brief=''),
    'WeldingSetCurrent': LuaFunction(
        name='WeldingSetCurrent', section='3.5.1 Welding',
        prototype='WeldingSetCurrent(ioType, current,blend,AOIndex)',
        arity=4, brief='Set welding current'),
    'WeldingSetCurrentGradualChangeEnd': LuaFunction(
        name='WeldingSetCurrentGradualChangeEnd', section='3.5.1 Welding',
        prototype='WeldingSetCurrentGradualChangeEnd( )',
        arity=0, brief='Set the end of the welding current'),
    'WeldingSetCurrentGradualChangeStart': LuaFunction(
        name='WeldingSetCurrentGradualChangeStart', section='3.5.1 Welding',
        prototype='WeldingSetCurrentGradualChangeStart(ioType, currentStart, currentEnd, aoIndex, blend)',
        arity=5, brief=''),
    'WeldingSetCurrertRelation': LuaFunction(
        name='WeldingSetCurrertRelation', section='3.5.1 Welding',
        prototype='WeldingSetCurrertRelation(currentMin, currentMax, outputVoltageMin, outputVoltageMax, AOIndex)',
        arity=5, brief=''),
    'WeldingSetProcessParam': LuaFunction(
        name='WeldingSetProcessParam', section='3.5.1 Welding',
        prototype='WeldingSetProcessParam(id, startCurrent, startVolage, startTime, weldCurrent, weldVoltage, endCurrent, endVoltage, endTime)',
        arity=9, brief='Set welding process parameter'),
    'WeldingSetVoltage': LuaFunction(
        name='WeldingSetVoltage', section='3.5.1 Welding',
        prototype='WeldingSetVoltage(ioType, voltage, blend ,AOIndex)',
        arity=4, brief='Set the welding voltage'),
    'WeldingSetVoltageGradualChangeEnd': LuaFunction(
        name='WeldingSetVoltageGradualChangeEnd', section='3.5.1 Welding',
        prototype='WeldingSetVoltageGradualChangeEnd( )',
        arity=0, brief='Set the end of the welding voltage'),
    'WeldingSetVoltageGradualChangeStart': LuaFunction(
        name='WeldingSetVoltageGradualChangeStart', section='3.5.1 Welding',
        prototype='WeldingSetVoltageGradualChangeStart(ioType, voltageStart, voltageEnd, aoIndex, blend)',
        arity=5, brief='Set the welding voltage gradient to'),
    'WeldingSetVoltageRelation': LuaFunction(
        name='WeldingSetVoltageRelation', section='3.5.1 Welding',
        prototype='WeldingSetVoltageRelation(currentMin, currentMax, outputVoltageMin, outputVoltageMax, AOIndex)',
        arity=5, brief=''),
    'WireSearchEnd': LuaFunction(
        name='WireSearchEnd', section='3.5.5 Wire positioning',
        prototype='WireSearchEnd(refPos, searchVel, searchDis, autoBackFlag, autoBackVel, autoBackDis, offectFlag)',
        arity=7, brief='End of wire positioning'),
    'WireSearchStart': LuaFunction(
        name='WireSearchStart', section='3.5.5 Wire positioning',
        prototype='WireSearchStart(refPos, searchVel, searchDis, autoBackFlag, autoBackVel, autoBackDis, offectFlag)',
        arity=7, brief='Wire positioning begins'),
    'WireSearchWait': LuaFunction(
        name='WireSearchWait', section='3.5.5 Wire positioning',
        prototype='WireSearchWait(varname)',
        arity=1, brief='Waiting for the completion of wire positioning'),
    'WorkPieceTrsfEnd': LuaFunction(
        name='WorkPieceTrsfEnd', section='3.2.17 Workpiece Conversion',
        prototype='WorkPieceTrsfEnd( )',
        arity=0, brief=''),
    'WorkPieceTrsfStart': LuaFunction(
        name='WorkPieceTrsfStart', section='3.2.17 Workpiece Conversion',
        prototype='WorkPieceTrsfStart(id)',
        arity=1, brief=''),
    'XmlrpcClientCall': LuaFunction(
        name='XmlrpcClientCall', section='3.7.2 Xmlrpc',
        prototype='XmlrpcClientCall(url, func, type, func_ Para)',
        arity=4, brief='Data Remote Call'),
    'dmpMotion': LuaFunction(
        name='dmpMotion', section='3.2.16 DMP',
        prototype='dmpMotion(joint_pos, desc_pos , tool, user, vel, acc, ovl, exaxis_pos )',
        arity=8, brief=''),
}


# Argument counts seen in the manual's own worked examples. An example
# SHORTER than the prototype is usually an OCR-eaten comma; an example
# LONGER than the prototype is a contradiction the manual cannot
# resolve, and OCR cannot invent an argument.
MANUAL_EXAMPLES: dict[str, list[int]] = {
    'ARCEnd': [2, 3],
    'ARCStart': [2, 3],
    'ActGripper': [2],
    'ArcWeldTraceControl': [17, 19],
    'AuxServoEnable': [2],
    'AuxServoHoming': [4],
    'AuxServoSetControlmode': [2],
    'ConveyorIODetect': [1],
    'ConveyorTrackStart': [1],
    'ExtAxisSetHoming': [4],
    'FT_Control': [23, 26, 35, 36, 37],
    'FT_FindSurface': [6],
    'FT_Guard': [27],
    'FT_LinInsertion': [5],
    'FT_SpiralSearch': [4],
    'FieldBusSlaveReadAI': [2],
    'FieldBusSlaveReadDI': [2],
    'FieldBusSlaveWaitAI': [4],
    'FieldBusSlaveWaitDI': [3],
    'GetAuxAI': [2],
    'GetAuxDI': [2],
    'GetAxleGenComCycleData': [1],
    'GetLaserWeldingParamActual': [1],
    'GetVirtualAI': [1],
    'GetVirtualDI': [1],
    'GetVirtualToolAI': [1],
    'GetVirtualToolDI': [1],
    'HorizonSpiralMotionStart': [4],
    'LTLaserOn': [1],
    'LTSearchStart': [6],
    'LTTrackOn': [1],
    'LaserSensorRecord': [10],
    'LoadPosSensorDriver': [1],
    'ModbusRegGetData': [2],
    'MoveAOStart': [2],
    'MoveDOStart': [3],
    'MoveGripper': [2, 6],
    'MoveToLaserRecordEnd': [2],
    'MoveToLaserRecordStart': [2],
    'MoveToolAOStart': [4],
    'MoveToolDOStart': [3],
    'NewSplinePoint': [19],
    'NewSplineStart': [2],
    'PTP': [4],
    'Pause': [1],
    'PointsOffsetEnable': [7],
    'PolishingDeviceEnable': [1],
    'PolishingSetWorkPieceWeight': [1],
    'SPLCSetAO': [2],
    'SPLCSetToolAO': [2],
    'SetAO': [3],
    'SetAuxAO': [3],
    'SetAuxDO': [4],
    'SetDO': [4],
    'SetDexterousHandsAct': [2],
    'SetLaserWeldingParam': [7],
    'SetOaccScale': [1],
    'SetToolAO': [3],
    'SetToolDO': [4],
    'SetVirtualAI': [2],
    'SetVirtualDI': [2],
    'SetVirtualToolAI': [2],
    'SetVirtualToolDI': [2],
    'SplineCIRC': [33],
    'SplineLINE': [17],
    'SplinePTP': [17],
    'ToolTrsfStart': [1],
    'UnloadPosSensorDriver': [1],
    'WaitAI': [4],
    'WaitAuxAI': [5],
    'WaitAuxDI': [4],
    'WaitDI': [4],
    'WaitMs': [1],
    'WaitMultiDI': [4, 5],
    'WaitToolAI': [4],
    'WaitToolDI': [4],
    'WeaveChangeStart': [4],
    'WeaveEnd': [1],
    'WeaveInspectEnd': [1],
    'WeaveInspectStart': [1],
    'WeaveStart': [1],
    'WeldingSetCurrent': [4],
    'WeldingSetCurrentGradualChangeStart': [5],
    'WeldingSetVoltage': [4],
    'WeldingSetVoltageGradualChangeStart': [5],
    'WireSearchEnd': [4, 7],
    'WireSearchStart': [4, 7],
    'WorkPieceTrsfStart': [1],
}


# Functions whose prototype is contradicted by the manual's own
# examples. FWS refuses to GENERATE these into a Lua program: emitting
# a call whose arity is unknowable from the documentation is how MoveL
# moved an arm 300 mm. Hand-written programs may still use them.
ARITY_CONFLICTS: dict[str, dict[str, object]] = {
    'ArcWeldTraceControl': {'prototype_arity': 17, 'example_arities': [17, 19]},
    'FT_Control': {'prototype_arity': 21, 'example_arities': [23, 26, 35, 36, 37]},
    'FT_Guard': {'prototype_arity': 26, 'example_arities': [27]},
    'FieldBusSlaveWaitAI': {'prototype_arity': 3, 'example_arities': [4]},
    'GetAuxDI': {'prototype_arity': 1, 'example_arities': [2]},
    'MoveToLaserRecordEnd': {'prototype_arity': 0, 'example_arities': [2]},
    'MoveToLaserRecordStart': {'prototype_arity': 0, 'example_arities': [2]},
    'NewSplinePoint': {'prototype_arity': 18, 'example_arities': [19]},
    'SetDexterousHandsAct': {'prototype_arity': 1, 'example_arities': [2]},
}


# Spellings that appear in the manual's section headings but are wrong.
# Using one produces a Lua nil-call at run time, on the controller,
# mid-program -- so they are recorded rather than silently dropped.
MANUAL_ERRATA: dict[str, dict[str, str | None]] = {
    'AuxServosetStatusID': {'correct': 'AuxServoSetStatusID', 'why': "prototype spelling; the SDK and the manual's own example both use AuxServoSetStatusID"},
    'AuxServosetStatusid': {'correct': 'AuxServoSetStatusID', 'why': "heading spelling; the SDK and the manual's own example both use AuxServoSetStatusID"},
    'ExtAxisSeroOn': {'correct': 'ExtAxisServoOn', 'why': 'OCR dropped the v in Servo'},
    'FT_ComplianeStop': {'correct': 'FT_ComplianceStop', 'why': "heading drops the 'c' in Compliance"},
    'FT_SotInsertion': {'correct': 'FT_RotInsertion', 'why': 'heading typo for Rot; prototype row says FT_RotInsertion'},
    'FT_Spiralsearch': {'correct': 'FT_SpiralSearch', 'why': 'heading typo; the prototype row spells it FT_SpiralSearch'},
    'GetWireSearchoffset': {'correct': 'GetWireSearchOffset', 'why': 'inconsistent capitalisation'},
    'ImpedanceControlStrartStop': {'correct': 'ImpedanceControlStartStop', 'why': 'heading doubles the r in Start'},
    'NewAuxthread': {'correct': 'NewAuxThread', 'why': 'inconsistent capitalisation'},
    'SPLCSDBI': {'correct': 'SPLCGetDI', 'why': 'OCR mangled the name; the body describes a non-blocking DI read'},
    'SPLCsetDO': {'correct': 'SPLCSetDO', 'why': 'inconsistent capitalisation'},
    'SetForwardwireFeed': {'correct': 'SetForwardWireFeed', 'why': 'inconsistent capitalisation'},
    'SetSrarionTrackPara': {'correct': 'SetStationTrackPara', 'why': 'OCR mangled Station'},
    'VDuxDI': {'correct': 'GetAuxDI', 'why': "OCR mangled the name; body describes 'Get Extended DI'"},
    'WeldingDicturrent': {'correct': 'WeldingSetCurrent', 'why': 'OCR mangled the name'},
    'WeldingGetCurrertRelation': {'correct': 'WeldingGetCurrentRelation', 'why': 'OCR read n as r'},
    'WeldingSetvoltage': {'correct': 'WeldingSetVoltage', 'why': 'inconsistent capitalisation'},
    'XMLrpcClientCall': {'correct': 'XmlrpcClientCall', 'why': 'inconsistent capitalisation'},
    'mode': {'correct': None, 'why': "the manual titles this section 'mode'; no such bare function -- mode switching is SetSysServoBootMode / the Mode RPC"},
}


def resolve(name: str) -> str | None:
    """Map a manual spelling to the name the interpreter accepts."""
    if name in LUA_FUNCTIONS:
        return name
    e = MANUAL_ERRATA.get(name)
    return e['correct'] if e else None


def by_section() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for n, f in LUA_FUNCTIONS.items():
        out.setdefault(f.section, []).append(n)
    return {k: sorted(v) for k, v in sorted(out.items())}
