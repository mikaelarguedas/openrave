#!/usr/bin/env python
# Copyright (C) 2009-2010 Rosen Diankov (rosen.diankov@gmail.com)
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import with_statement # for python 2.5
__author__ = 'Rosen Diankov'
__copyright__ = 'Copyright (C) 2009-2010 Rosen Diankov (rosen.diankov@gmail.com)'
__license__ = 'Apache License, Version 2.0'

import time
from numpy import *

from openravepy import *
from openravepy.databases import inversekinematics, kinematicreachability
from openravepy.interfaces import BaseManipulation, TaskManipulation, VisualFeedback

class VisibilityModel(OpenRAVEModel):
    class GripperVisibility:
        """Used to hide links not beloning to gripper.

        When 'entered' will hide all the non-gripper links in order to facilitate visiblity of the gripper
        """
        def __init__(self,manip):
            self.manip = manip
            self.robot = self.manip.GetRobot()
            self.hiddengeoms = []
        def __enter__(self):
            self.hiddengeoms = []
            with self.robot.GetEnv():
                # stop rendering the non-gripper links
                childlinkids = [link.GetIndex() for link in self.manip.GetChildLinks()]
                for link in self.robot.GetLinks():
                    if link.GetIndex() not in childlinkids:
                        for geom in link.GetGeometries():
                            self.hiddengeoms.append((geom,geom.IsDraw()))
                            geom.SetDraw(False)
        def __exit__(self,type,value,traceback):
            with self.robot.GetEnv():
                for geom,isdraw in self.hiddengeoms:
                    geom.SetDraw(isdraw)

    def __init__(self,robot,target,sensorrobot=None,sensorname=None,maxvelmult=None):
        """Starts a visibility model using a robot, a sensor, and a target

        The minimum needed to be specified is the robot and a sensorname. Supports sensors that do
        not belong to the current robot in the case that a robot is holding the target with its
        manipulator. Providing the target allows visibility information to be computed.
        """
        OpenRAVEModel.__init__(self,robot=robot)
        self.sensorrobot = sensorrobot if sensorrobot is not None else robot
        self.target = target
        self.visualprob = VisualFeedback(self.robot,maxvelmult=maxvelmult)
        self.basemanip = BaseManipulation(self.robot,maxvelmult=maxvelmult)
        self.convexhull = None
        self.sensorname = sensorname
        self.manip = robot.GetActiveManipulator()
        self.manipname = None if self.manip is None else self.manip.GetName()
        self.visibilitytransforms = None
        self.rmodel = self.ikmodel = None
        self.preshapes = None
        self.preprocess()
    def clone(self,envother):
        clone = OpenRAVEModel.clone(self,envother)
        clone.rmodel = self.rmodel.clone(envother) if not self.rmodel is None else None
        clone.preshapes = array(self.preshapes) if not self.preshapes is None else None
        clone.ikmodel = self.ikmodel.clone(envother) if not self.ikmodel is None else None
        clone.visualprob = self.visualprob.clone(envother)
        clone.basemanip = self.basemanip.clone(envother)
        clone.preprocess()
        return clone
    def getversion(self):
        return 1
    def has(self):
        return self.visibilitytransforms is not None and len(self.visibilitytransforms) > 0
    def getversion(self):
        return 1
    def getfilename(self):
        return os.path.join(OpenRAVEModel.getfilename(self),'visibility.' + self.manip.GetStructureHash() + '.' + self.attachedsensor.GetStructureHash() + '.' + self.target.GetKinematicsGeometryHash()+'.pp')
    def load(self):
        try:
            params = OpenRAVEModel.load(self)
            if params is None:
                return False
            self.visibilitytransforms,self.convexhull,self.KK,self.dims,self.preshapes = params
            self.preprocess()
            return self.has()
        except e:
            return False
    def save(self):
        OpenRAVEModel.save(self,(self.visibilitytransforms,self.convexhull,self.KK,self.dims,self.preshapes))

    def preprocess(self):
        with self.env:
            manipname = self.visualprob.SetCameraAndTarget(sensorname=self.sensorname,sensorrobot=self.sensorrobot,manipname=self.manipname,target=self.target)
            assert(self.manipname is None or self.manipname==manipname)
            self.manip = self.robot.SetActiveManipulator(manipname)
            self.attachedsensor = [s for s in self.sensorrobot.GetSensors() if s.GetName() == self.sensorname][0]
            self.ikmodel = inversekinematics.InverseKinematicsModel(robot=self.robot,iktype=IkParameterization.Type.Transform6D)
            if not self.ikmodel.load():
                self.ikmodel.autogenerate()
            if self.visibilitytransforms is not None:
                self.visualprob.SetCameraTransforms(transforms=self.visibilitytransforms)
    
    def autogenerate(self,options=None,gmodel=None):
        preshapes = None
        if options is not None:
            if options.preshapes is not None:
                preshapes = zeros((0,len(self.manip.GetGripperJoints())))
                for preshape in options.preshapes:
                    preshapes = r_[preshapes,[array([float(s) for s in preshape.split()])]]
        if not gmodel is None:
            preshapes = array([gmodel.grasps[0][gmodel.graspindices['igrasppreshape']]])
        if preshapes is None:
            with self.target:
                self.target.Enable(False)
                taskmanip = TaskManipulation(self.robot)
                final,traj = taskmanip.ReleaseFingers(execute=False,outputfinal=True)
            preshapes = array([final])
        self.generate(preshapes=preshapes)
        self.save()
    def generate(self,preshapes):
        self.preshapes=preshapes
        self.preprocess()
        self.sensorname = self.attachedsensor.GetName()
        self.manipname = self.manip.GetName()
        bodies = [(b,b.IsEnabled()) for b in self.env.GetBodies() if b != self.robot and b != self.target]
        for b in bodies:
            b[0].Enable(False)
        try:
            with self.env:
                sensor = self.attachedsensor.GetSensor()
                if sensor is not None: # set power to 0?
                    sensordata = sensor.GetSensorData()
                    self.KK = sensordata.KK
                    self.dims = sensordata.imagedata.shape
                with RobotStateSaver(self.robot):
                    # find better way of handling multiple grasps
                    self.robot.SetJointValues(self.preshapes[0],self.manip.GetGripperJoints())
                    extentsfile = os.path.join(self.env.GetHomeDirectory(),'kinbody.'+self.target.GetKinematicsGeometryHash(),'visibility.txt')
                    if os.path.isfile(extentsfile):
                        self.visibilitytransforms = self.visualprob.ProcessVisibilityExtents(extents=loadtxt(extentsfile,float))
                    else:
                        self.visibilitytransforms = self.visualprob.ProcessVisibilityExtents(sphere=[3,0.1,0.15,0.2,0.25,0.3])
                self.visualprob.SetCameraTransforms(transforms=self.visibilitytransforms)
        finally:
            for b,enable in bodies:
                b.Enable(enable)

    def SetCameraTransforms(self,transforms):
        self.visualprob.SetCameraTransforms(transforms=transforms)
    def showtransforms(self):
        pts = array([dot(self.target.GetTransform(),matrixFromPose(pose))[0:3,3] for pose in self.visibilitytransforms])
        h=self.env.plot3(pts,5,colors=array([0.5,0.5,1,0.2]))
        with RobotStateSaver(self.robot):
            with self.GripperVisibility(self.manip):
                for pose in self.visibilitytransforms:
                    with self.env:
                        self.robot.SetJointValues(self.preshapes[0],self.manip.GetGripperJoints())
                        Trelative = dot(linalg.inv(self.attachedsensor.GetTransform()),self.manip.GetEndEffectorTransform())
                        Tcamera = dot(self.target.GetTransform(),matrixFromPose(pose))
                        Tgrasp = dot(Tcamera,Trelative)
                        Tdelta = dot(Tgrasp,linalg.inv(self.manip.GetEndEffectorTransform()))
                        for link in self.manip.GetChildLinks():
                            link.SetTransform(dot(Tdelta,link.GetTransform()))
                        visibility = self.visualprob.ComputeVisibility()
                        self.env.UpdatePublishedBodies()
                    raw_input('visibility %d, press any key to continue: '%visibility)
    def show(self,options=None):
        self.env.SetViewer('qtcoin')
        return self.showtransforms()
    def moveToPreshape(self):
        """uses a planner to safely move the hand to the preshape and returns the trajectory"""
        preshape=self.preshapes[0]
        with self.robot:
            self.robot.SetActiveDOFs(self.manip.GetArmJoints())
            self.basemanip.MoveUnsyncJoints(jointvalues=preshape,jointinds=self.manip.GetGripperJoints())
        while not self.robot.GetController().IsDone(): # busy wait
            time.sleep(0.01)        
        with self.robot:
            self.robot.SetActiveDOFs(self.manip.GetGripperJoints())
            self.basemanip.MoveActiveJoints(goal=preshape)
        while not self.robot.GetController().IsDone(): # busy wait
            time.sleep(0.01)

    def computeValidTransform(self,returnall=False,checkcollision=True,computevisibility=True,randomize=False):
        with self.robot:
            validjoints = []
            if randomize:
                order = random.permutation(len(self.visibilitytransforms))
            else:
                order = xrange(len(self.visibilitytransforms))
            for i in order:
                pose = self.visibilitytransforms[i]
                Trelative = dot(linalg.inv(self.attachedsensor.GetTransform()),self.manip.GetEndEffectorTransform())
                Tcamera = dot(self.target.GetTransform(),matrixFromPose(pose))
                Tgrasp = dot(Tcamera,Trelative)
                s = self.manip.FindIKSolution(Tgrasp,checkcollision)
                if s is not None:
                    self.robot.SetJointValues(s,self.manip.GetArmJoints())
                    if computevisibility and not self.visualprob.ComputeVisibility():
                        continue
                    validjoints.append((s,i))
                    if not returnall:
                        return validjoints
                    print 'found',len(validjoints)

    def pruneTransformations(self,thresh=0.04,numminneighs=10,maxdist=None,translationonly=True):
        if self.rmodel is None:
            self.rmodel = kinematicreachability.ReachabilityModel(robot=self.robot)
            if not self.rmodel.load():
                # do not autogenerate since that would force this model to depend on the reachability
                self.rmodel = None
                return array(visibilitytransforms)
        kdtree=self.rmodel.ComputeNN(translationonly)
        if maxdist is not None:
            visibilitytransforms = self.visibilitytransforms[invertPoses(self.visibilitytransforms)[:,6]<maxdist]
        else:
            visibilitytransforms = self.visibilitytransforms
        newtrans = poseMultArrayT(poseFromMatrix(dot(linalg.inv(self.manip.GetBase().GetTransform()),self.target.GetTransform())),visibilitytransforms)
        if translationonly:
            transdensity = kdtree.kFRSearchArray(newtrans[:,4:7],thresh**2,0,thresh*0.01)[2]
            I=flatnonzero(transdensity>numminneighs)
            return visibilitytransforms[I[argsort(-transdensity[I])]]
        raise ValueError('not supported')
#         Imask = GetCameraRobotMask(orenv,options.robotfile,sensorindex=options.sensorindex,gripperjoints=gripperjoints,robotjoints=robotjoints,robotjointinds=robotjointinds,rayoffset=options.rayoffset)
#         # save as a ascii matfile
#         numpy.savetxt(options.savefile,Imask,'%d')
#         print 'mask saved to ' + options.savefile
#         try:
#             scipy.misc.pilutil.imshow(array(Imask*255,'uint8'))
#         except:
#             pass

#     def GetCameraRobotMask(self,rayoffset=0):
#         with self.env:
#             inds = array(range(self.width*self.height))
#             imagepoints = array((mod(inds,self.width),floor(inds/self.width)))
#             camerapoints = dot(linalg.inv(self.KK), r_[imagepoints,ones((1,imagepoints.shape[1]))])
#             Tcamera = self.attached.GetSensor().GetTransform()
#             raydirs = dot(Tcamera[0:3,0:3], camerapoints / tile(sqrt(sum(camerapoints**2,0)),(3,1)))
#             rays = r_[tile(Tcamera[0:3,3:4],(1,raydirs.shape[1]))+rayoffset*raydirs,100.0*raydirs]
#             hitindices,hitpositions = self.prob.GetEnv().CheckCollisionRays(rays,self.robot,False)
#             # gather all the rays that hit and form an image
#             return reshape(array(hitindices,'float'),(height,width))

    def getCameraImage(self,delay=1.0):
        sensor=self.attachedsensor.GetSensor()
        sensor.SendCommand('power 1')
        try:
            time.sleep(delay)
            return sensor.GetSensorData().imagedata
        finally:
            sensor.SendCommand('power 0')

    @staticmethod
    def CreateOptionParser():
        parser = OpenRAVEModel.CreateOptionParser()
        parser.description='Computes and manages the visibility transforms for a manipulator/target.'
        parser.add_option('--target',action="store",type='string',dest='target',
                          help='OpenRAVE kinbody target filename')
        parser.add_option('--sensorname',action="store",type='string',dest='sensorname',default=None,
                          help='Name of the sensor to build visibilty model for (has to be camera). If none, takes first possible sensor.')
        parser.add_option('--preshape', action='append', type='string',dest='preshapes',default=None,
                          help='Add a preshape for the manipulator gripper joints')
        parser.add_option('--rayoffset',action="store",type='float',dest='rayoffset',default=0.03,
                          help='The offset to move the ray origin (prevents meaningless collisions), default is 0.03')
        return parser
    @staticmethod
    def RunFromParser(Model=None,parser=None):
        if parser is None:
            parser = VisibilityModel.CreateOptionParser()
        (options, args) = parser.parse_args()
        env = Environment()
        try:
            target = None
            with env:
                target = env.ReadKinBodyXMLFile(options.target)
                target.SetTransform(eye(4))
                env.AddKinBody(target)
            if Model is None:
                Model = lambda robot: VisibilityModel(robot=robot,target=target,sensorname=options.sensorname)
            OpenRAVEModel.RunFromParser(env=env,Model=Model,parser=parser)
        finally:
            env.Destroy()

if __name__=='__main__':
    VisibilityModel.RunFromParser()