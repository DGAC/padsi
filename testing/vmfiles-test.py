#!/usr/bin/python3

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

import os
import tempfile
import unittest

from padsi.run.vm.admin import AdminVMFiles
from padsi.run.vm.version import VMState, VMVersion, VMVersionType
from padsi.run.vm.vmfiles import VMFiles


def debug(testdir:tempfile.TemporaryDirectory):
    print(f"==> {testdir.name}")

class VMVersionTest(unittest.TestCase):
    testdir:tempfile.TemporaryDirectory
    uid:int
    gid:int
    lastv:int=0

    @classmethod
    def setUpClass(cls):
        cls.testdir=tempfile.TemporaryDirectory()
        cls.uid=os.geteuid()
        cls.gid=os.getegid()

    @classmethod
    def tearDownClass(cls):
        cls.testdir.cleanup()

    @classmethod
    def get_unused_version_number(cls) -> int:
        cls.lastv+=1
        return cls.lastv

    def test_base(self):
        v=self.__class__.get_unused_version_number()
        vmversion=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, v)

        self.assertFalse(vmversion.is_complete)
        self.assertEqual(vmversion.version_number, v)

        vmversion=VMVersion(VMVersionType.BASE, self.__class__.testdir.name)
        self.assertFalse(vmversion.is_complete)
        self.assertEqual(vmversion.version_number, None)

    def test_attrs(self):
        v=self.__class__.get_unused_version_number()
        vmversion=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, v)
        self.assertFalse(vmversion.is_complete)

        # test state
        self.assertEqual(vmversion.state, None)
        vmversion.set_state(VMState.DISCARDED)

        self.assertTrue(vmversion.state==VMState.DISCARDED)
        self.assertTrue(os.path.isfile(vmversion.infos_file))
        self.assertCountEqual(os.listdir(self.__class__.testdir.name), [f"base.{v}.infos"])

        # test files discard and creation
        vmversion.discard_files()
        self.assertCountEqual(os.listdir(self.__class__.testdir.name), [])
        vmversion.initialize_files(1)
        self.assertTrue(vmversion.is_complete)
        self.assertTrue(os.path.isfile(os.path.join(self.__class__.testdir.name, f"base.{v}.img")))
        self.assertTrue(os.path.isfile(os.path.join(self.__class__.testdir.name, f"base.{v}.vars")))
        self.assertTrue(os.path.isfile(os.path.join(self.__class__.testdir.name, f"base.{v}.infos")))


    def test_copy(self):
        vmversion=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, self.__class__.get_unused_version_number())
        vmversion.set_state(VMState.CREATED)

        target=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, self.__class__.get_unused_version_number())
        self.assertRaises(Exception, vmversion.copy, target)
        self.assertFalse(target.is_complete)

        vmversion.initialize_files(1)
        vmversion.copy(target)
        self.assertTrue(target.is_complete)

        self.assertFalse(vmversion.derives_from(target))
        self.assertFalse(target.derives_from(vmversion))

    def test_derive(self):
        vmversion=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, self.__class__.get_unused_version_number())
        target=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, self.__class__.get_unused_version_number())

        vmversion.initialize_files(1)
        vmversion.derive(target)
        self.assertTrue(target.is_complete)

        self.assertFalse(vmversion.derives_from(target))
        self.assertTrue(target.derives_from(vmversion))

    def test_move(self):
        vmversion=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, self.__class__.get_unused_version_number())
        target=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, self.__class__.get_unused_version_number())

        vmversion.initialize_files(1)
        vmversion.move(target)
        self.assertFalse(vmversion.is_complete)
        self.assertTrue(target.is_complete)

    def test_serialize(self):
        vmv1=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, self.__class__.get_unused_version_number())
        ser1=vmv1.serialize()
        vmv2=VMVersion.deserialize(ser1)
        self.assertEqual(vmv1, vmv2)
        ser2=vmv2.serialize()
        self.assertEqual(ser1, ser2)

        vmv1=VMVersion(VMVersionType.BASE, self.__class__.testdir.name, None)
        ser1=vmv1.serialize()
        vmv2=VMVersion.deserialize(ser1)
        self.assertEqual(vmv1, vmv2)
        ser2=vmv2.serialize()
        self.assertEqual(ser1, ser2)

class VMFilesTest(unittest.TestCase):
    def test_init(self):
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            self.assertEqual(len(os.listdir(vmf.directory)), 0)

            vmf.create_common_dirs()
            self.assertCountEqual(os.listdir(vmf.directory), ["staging", "zones"])

            self.assertEqual(vmf.base_versions, [])
            self.assertEqual(vmf.last_base_version, None)
            self.assertEqual(vmf.next_base_version_number, 0)
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_discard(self):
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            base=VMVersion(VMVersionType.BASE, vmf.directory, 0)
            base.initialize_files(1)
            base.set_state(VMState.STOPPED)

            self.assertTrue(base.is_complete)
            self.assertFalse(base.is_nonexisting)
            base.discard_files()
            self.assertFalse(base.is_complete)
            self.assertTrue(base.is_nonexisting)
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_staging_create(self):
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            staged=vmf.stage_from_scratch(VMVersionType.BASE, 1)
            self.assertTrue(staged.is_complete)
            self.assertEqual(staged.version_number, None)
            self.assertEqual(vmf.get_staged(VMVersionType.BASE), staged)

            staged2=vmf.stage_from_scratch(VMVersionType.BASE, 1)
            self.assertEqual(staged2.version_number, None)
            self.assertEqual(vmf.get_staged(VMVersionType.BASE), staged2)
            staged.discard_files() # cleanup for other tests
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_staging_from(self):
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            vmf.create_common_dirs()
            os.makedirs(vmf.staging_directory, exist_ok=True)

            base=VMVersion(VMVersionType.BASE, vmf.directory, 0)
            base.initialize_files(1)
            base.set_state(VMState.STOPPED)
            staged=vmf.stage_existing_version(base)
            self.assertTrue(staged.is_complete)
            self.assertEqual(staged.version_number, None)
            self.assertTrue(staged.derives_from(base))
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_publish(self):
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            staged=VMVersion(VMVersionType.BASE, vmf.directory)
            staged.initialize_files(1)
            staged.set_state(VMState.STOPPED)
            published=vmf.publish_staged(staged)
            self.assertTrue(published.is_complete)
            self.assertFalse(staged.is_complete)
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_user(self):
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            base=VMVersion(VMVersionType.BASE, vmf.directory, 0)
            base.initialize_files(1)
            base.set_state(VMState.STOPPED)

            user=vmf.create_user_version("thezone", base)
            self.assertTrue(user.is_complete)
            self.assertTrue(base.is_complete)
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_cycle_simple(self):
        """Steps:
        - create a BASE VM version
        - stage it
        - publish it
        """
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            vmf.create_common_dirs()
            os.makedirs(vmf.staging_directory, exist_ok=True)

            base=VMVersion(VMVersionType.BASE, vmf.directory, 5)
            base.initialize_files(1)
            base.set_state(VMState.STOPPED)

            staged=vmf.stage_existing_version(base)
            self.assertEqual(staged.backing_image_file, base.image_file)

            published=vmf.publish_staged(staged)
            self.assertTrue(published.is_complete)

            # staged has been moved and does not exist anymore
            self.assertTrue(staged.is_nonexisting)

            # published VM version still depends on base
            self.assertEqual(published.backing_image_file, base.image_file)

            base.discard_files()
            published.discard_files()
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_cycle_with_other_snapshot(self):
        """Steps:
        - create a BASE VM version
        - stage it
        - create a USER version based on the BASE VM version
        - publish the staged version
        """
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            vmf.create_common_dirs()
            os.makedirs(vmf.staging_directory, exist_ok=True)

            base=VMVersion(VMVersionType.BASE, vmf.directory, 5)
            base.initialize_files(1)
            base.set_state(VMState.STOPPED)

            staged=vmf.stage_existing_version(base)
            self.assertEqual(staged.backing_image_file, base.image_file)

            vmf.create_user_version("thezone", base)

            published=vmf.publish_staged(staged)
            self.assertTrue(published.is_complete)
            self.assertTrue(staged.is_nonexisting)
            self.assertEqual(published.backing_image_file, base.image_file)

            base.discard_files()
            published.discard_files()
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_obsolete(self):
        """Steps:
        - create a BASE VM version => will be obsolete
        - create a 2nd BASE VM version
        - stage that 2nd BASE VM version => base2 is not obsolete
        - create a 3rd BASE VM version
        - create a USER version from base 3 => base3 is not obsolete
        - create a 4th BASE VM version => not obsolete as it's the latest
        """
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            vmf.create_common_dirs()
            os.makedirs(vmf.staging_directory, exist_ok=True)

            base1=VMVersion(VMVersionType.BASE, vmf.directory, 1)
            base1.initialize_files(1)
            base1.set_state(VMState.STOPPED)
            vmf.declare_version_object(base1)

            base2=VMVersion(VMVersionType.BASE, vmf.directory, 2)
            base2.initialize_files(1)
            base2.set_state(VMState.STOPPED)

            vmf.stage_existing_version(base2)

            base3=VMVersion(VMVersionType.BASE, vmf.directory, 3)
            base3.initialize_files(1)
            base3.set_state(VMState.STOPPED)

            vmf.create_user_version("thezone", base3)

            base4=VMVersion(VMVersionType.BASE, vmf.directory, 4)
            base4.initialize_files(1)
            base4.set_state(VMState.STOPPED)

            self.assertEqual(vmf.obsolete_versions, [base1])
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_committable(self):
        """Steps:
        - create a BASE VM version
        - stage that version
        - publish it => will be committable
        - create a BASE VM version
        - stage that version
        - publish it
        - create a USER version based on that 2nd published version which should still be committable even though
          it has a USER version which depens on it
        """
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            vmf.create_common_dirs()
            os.makedirs(vmf.staging_directory, exist_ok=True)

            base1=VMVersion(VMVersionType.BASE, vmf.directory, 1)
            base1.initialize_files(1)
            base1.set_state(VMState.STOPPED)

            staged=vmf.stage_existing_version(base1)
            published1=vmf.publish_staged(staged)

            base2=VMVersion(VMVersionType.BASE, vmf.directory, 3)
            base2.initialize_files(1)
            base2.set_state(VMState.STOPPED)

            staged=vmf.stage_existing_version(base2)
            published2=vmf.publish_staged(staged)
            user=vmf.create_user_version("thezone", published2)

            self.assertCountEqual(vmf.committable_versions, [published1, published2])

            # commit published1
            commit1=VMVersion(VMVersionType.BASE, vmf.directory, vmf.next_base_version_number)
            vmf.commit_version(published1, commit1)
            self.assertTrue(base1.is_nonexisting)
            self.assertTrue(published1.is_nonexisting)
            self.assertTrue(commit1.is_complete)

            # commit published2
            commit2=VMVersion(VMVersionType.BASE, vmf.directory, vmf.next_base_version_number)
            vmf.commit_version(published2, commit2)
            self.assertTrue(published2.is_nonexisting)
            self.assertTrue(commit2.is_complete)

            self.assertEqual(user.backing_image_file, commit2.image_file)
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_commit_in_place(self):
        """Steps:
        - create a BASE VM version
        - stage that version
        - publish it
        - create a user VM version
        - commit the published VM version without changing the version
        """
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            vmf.create_common_dirs()
            os.makedirs(vmf.staging_directory, exist_ok=True)

            base=VMVersion(VMVersionType.BASE, vmf.directory, 1)
            base.initialize_files(1)
            base.set_state(VMState.STOPPED)

            staged=vmf.stage_existing_version(base)
            published=vmf.publish_staged(staged)

            self.assertTrue(published.is_complete)
            self.assertEqual(published.backing_image_file, base.image_file)

            user=vmf.create_user_version("thezone", published)
            self.assertEqual(user.backing_image_file, published.image_file)

            vmf.commit_version(published)
            self.assertTrue(published.is_complete)
            self.assertEqual(published.backing_image_file, None)
            self.assertEqual(user.backing_image_file, published.image_file)
            self.assertTrue(base.is_nonexisting)
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_serialize(self):
        testdir=None
        try:
            # setup
            testdir=tempfile.TemporaryDirectory()
            vmf=VMFiles(testdir.name)
            vmf.create_common_dirs()
            os.makedirs(vmf.staging_directory, exist_ok=True)

            base=VMVersion(VMVersionType.BASE, vmf.directory, 5)
            base.initialize_files(1)
            base.set_state(VMState.STOPPED)

            staged=vmf.stage_existing_version(base)
            vmf.create_user_version("thezone", base)

            published=vmf.publish_staged(staged)

            base.discard_files()
            published.discard_files()

            # actual test
            ser1=vmf.serialize()
            vmf2=VMFiles.deserialize(ser1)
            ser2=vmf2.serialize()
            self.assertCountEqual(ser1, ser2)
        finally:
            if testdir is not None:
                testdir.cleanup()

class AdminVMTest(unittest.TestCase):
    def test_0(self):
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()

            # for user 1
            vmf1=VMFiles(testdir.name, uid=1000, gid=1000)
            vmf1.create_common_dirs()
            os.makedirs(vmf1.staging_directory, exist_ok=True)

            base1=VMVersion(VMVersionType.BASE, vmf1.directory, 1) # will be obsolete
            base1.initialize_files(1)
            base1.set_state(VMState.STOPPED)

            base2=VMVersion(VMVersionType.BASE, vmf1.directory, 2)
            base2.initialize_files(1)
            base2.set_state(VMState.STOPPED)

            vmf1.refresh()
            self.assertEqual(vmf1.last_base_version, base2)
            assert(vmf1.last_base_version is not None)
            vmf1.create_user_version("thezone", vmf1.last_base_version)
            vmf1.create_snaphot_version("thezone", vmf1.last_user_version)
            staged=vmf1.stage_existing_version(vmf1.last_base_version)
            base3=vmf1.publish_staged(staged)

            with open(os.path.join(testdir.name, "extra1"), "w") as fd:
                fd.write("extra")
            with open(os.path.join(vmf1.staging_directory, "extra2"), "w") as fd:
                fd.write("extra")

            # for user 2
            vmf2=VMFiles(testdir.name, 2000, 2000)
            vmf2.create_common_dirs()
            os.makedirs(vmf2.staging_directory, exist_ok=True)

            vmf2.refresh()
            self.assertEqual(vmf2.last_base_version, base3)
            vmf2.create_user_version("thezone", vmf2.last_base_version) # will be obsolete
            vmf2.create_user_version("thezone", vmf2.last_base_version)

            os.makedirs(vmf2.get_zone_directory("thezone"), exist_ok=True)
            with open(os.path.join(vmf2.get_zone_directory("thezone"), "extra4"), "w") as fd:
                fd.write("extra")

            # verify the base images
            vmf1.refresh()
            vmf2.refresh()
            avmf=AdminVMFiles(testdir.name, 1000)
            self.assertEqual(vmf1.base_versions, avmf.base_versions)
            self.assertEqual(vmf2.base_versions, avmf.base_versions)

            # verify user versions
            self.assertEqual(vmf1.user_versions, avmf.user_versions(1000))
            self.assertEqual(vmf2.user_versions, avmf.user_versions(2000))

            # snapshot user versions
            self.assertEqual(vmf1.snapshot_versions, avmf.snapshot_versions(1000))
            self.assertEqual(vmf2.snapshot_versions, avmf.snapshot_versions(2000))

            # other stuff
            self.assertCountEqual(vmf1.obsolete_versions, [VMVersion(VMVersionType.BASE, vmf1.directory, 1)])
            self.assertCountEqual(vmf2.obsolete_versions, [VMVersion(VMVersionType.BASE, vmf1.directory, 1), VMVersion(VMVersionType.USER, vmf2.get_zone_directory("thezone"), 0)])
            self.assertCountEqual(avmf.obsolete_versions, vmf2.obsolete_versions)

            # unused files
            sa=set(avmf.unused_files)
            sb=set(vmf1.unused_files).union(vmf2.unused_files)
            self.assertEqual(sa, sb)

            # obsolete versions
            sa=set(avmf.obsolete_versions)
            sb=set(vmf1.obsolete_versions).union(vmf2.obsolete_versions)
            self.assertEqual(sa, sb)

            # committable versions, situation:
            # base.1.img
            # base.2.img
            # base.3.img ==> base.2.img
            # zones/thezone/2000/user.1.img ==> base.3.img
            # zones/thezone/2000/user.0.img ==> base.3.img
            # zones/thezone/1000/snap.0.img ==> zones/thezone/1000/user.0.img
            # zones/thezone/1000/user.0.img ==> base.2.img
            #
            # Notes:
            # - for user 2000, the base.3 can be committed (merged) into base.2 because no other
            #   VM image file hase base.2 as a backing image
            # - for user 1000, this is not the case because user.0 also uses base.2 as its backing
            #   image
            # - in the global view, then base.3 can't be merged into base.2
            self.assertEqual(vmf1.committable_versions, [])
            self.assertEqual(vmf2.committable_versions, [base3])
            self.assertEqual(avmf.committable_versions, [])
        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_1(self):
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()

            # for user 1
            vmf1=VMFiles(testdir.name, uid=1000, gid=1000)
            vmf1.create_common_dirs()
            os.makedirs(vmf1.staging_directory, exist_ok=True)

            base1=VMVersion(VMVersionType.BASE, vmf1.directory, 1)
            base1.initialize_files(1)
            base1.set_state(VMState.STOPPED)
            vmf1.refresh()
            assert(vmf1.last_base_version is not None)
            vmf1.stage_existing_version(vmf1.last_base_version)

            # for user 2
            vmf2=VMFiles(testdir.name, 2000, 2000)
            vmf2.create_common_dirs()
            os.makedirs(vmf2.staging_directory, exist_ok=True)

            base2=VMVersion(VMVersionType.BASE, vmf2.directory, 2)
            base2.initialize_files(1)
            base2.set_state(VMState.STOPPED)
            vmf2.refresh()
            assert(vmf2.last_base_version is not None)
            vmf2.stage_existing_version(vmf2.last_base_version)

            # staged VM versions verifications
            vmf1.refresh()
            avmf=AdminVMFiles(testdir.name, 1000)
            self.assertEqual(vmf1.base_versions, avmf.base_versions)
            self.assertEqual(vmf2.base_versions, avmf.base_versions)

            self.assertEqual(vmf1.get_staged(VMVersionType.BASE), avmf.get_staged(VMVersionType.BASE, 1000))
            self.assertEqual(vmf2.get_staged(VMVersionType.BASE), avmf.get_staged(VMVersionType.BASE, 2000))

        finally:
            if testdir is not None:
                testdir.cleanup()

    def test_3(self):
        testdir=None
        try:
            testdir=tempfile.TemporaryDirectory()

            # for user 1
            vmf1=VMFiles(testdir.name, uid=1000, gid=1000)
            vmf1.create_common_dirs()
            os.makedirs(vmf1.staging_directory, exist_ok=True)

            base1=VMVersion(VMVersionType.BASE, vmf1.directory, 1)
            base1.initialize_files(1)
            base1.set_state(VMState.STOPPED)
            vmf1.refresh()
            assert(vmf1.last_base_version is not None)
            vmf1.stage_existing_version(vmf1.last_base_version)

            # for user 2
            vmf2=VMFiles(testdir.name, 2000, 2000)
            vmf2.create_common_dirs()
            os.makedirs(vmf2.staging_directory, exist_ok=True)

            base2=VMVersion(VMVersionType.BASE, vmf2.directory, 2)
            base2.initialize_files(1)
            base2.set_state(VMState.STOPPED)
            vmf2.refresh()
            assert(vmf2.last_base_version is not None)
            vmf2.stage_existing_version(vmf2.last_base_version)

            # staged VM versions verifications
            vmf1.refresh()
            avmf=AdminVMFiles(testdir.name, 1000)
            ser1=avmf.serialize()
            avmf2=AdminVMFiles.deserialize(ser1)
            ser2=avmf2.serialize()
            self.assertCountEqual(ser1, ser2)

        finally:
            if testdir is not None:
                testdir.cleanup()

if __name__=='__main__':
    unittest.main()
