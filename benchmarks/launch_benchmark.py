#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2018 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: EPL-2.0
#

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import glob
import os
import signal
import subprocess
import sys
from argparse import ArgumentParser
from common import base_benchmark_util
from common.utils.validators import check_no_spaces


class LaunchBenchmark(base_benchmark_util.BaseBenchmarkUtil):
    """Launches benchmarking job based on the specified args """

    def __init__(self, *args, **kwargs):
        super(LaunchBenchmark, self).__init__(*args, **kwargs)

        self.args, self.unknown_args = self.parse_args()
        try:
            self.validate_args()
        except (IOError, ValueError) as e:
            sys.exit("\nError: {}".format(e))

    def main(self):
        benchmark_scripts = os.path.dirname(os.path.realpath(__file__))
        use_case = self.get_model_use_case(benchmark_scripts)
        intelai_models = self.get_model_dir(benchmark_scripts, use_case)
        env_var_dict = self.get_env_vars(benchmark_scripts, use_case, intelai_models)

        if self.args.docker_image:
            self.run_docker_container(benchmark_scripts, intelai_models, env_var_dict)
        else:
            self.run_bare_metal(benchmark_scripts, intelai_models, env_var_dict)

    def parse_args(self):
        # Additional args that are only used with the launch script
        arg_parser = ArgumentParser(
            parents=[self._common_arg_parser],
            description="Parse args for benchmark interface")

        arg_parser.add_argument(
            "--docker-image",
            help="Specify the docker image/tag to use when running benchmarking within a container."
                 "If no docker image is specified, then no docker container will be used.",
            dest="docker_image", default=None, type=check_no_spaces)

        arg_parser.add_argument(
            "--debug", help="Launches debug mode which doesn't execute "
                            "start.sh when running in a docker container.", action="store_true")

        return arg_parser.parse_known_args()

    def validate_args(self):
        """validate the args"""
        # validate that we support this framework by checking folder names
        benchmark_dir = os.path.dirname(os.path.realpath(__file__))
        if glob.glob("{}/*/{}".format(benchmark_dir, self.args.framework)) == []:
            raise ValueError("The specified framework is not supported: {}".
                             format(self.args.framework))

        # if neither benchmark_only or accuracy_only are specified, then enable
        # benchmark_only as the default
        if not self.args.benchmark_only and not self.args.accuracy_only:
            self.args.benchmark_only = True

    def get_model_use_case(self, benchmark_scripts):
        """
        Infers the use case based on the directory structure for the specified model.
        """
        args = self.args

        # find the path to the model's benchmarks folder
        search_path = os.path.join(
            benchmark_scripts, "*", args.framework, args.model_name,
            args.mode, args.precision)
        matches = glob.glob(search_path)
        error_str = ""
        if len(matches) > 1:
            error_str = "Found multiple model locations for {} {} {}"
        elif len(matches) == 0:
            error_str = "No model was found for {} {} {}"
        if error_str:
            raise ValueError(error_str.format(args.framework, args.model_name, args.precision))

        # use the benchmarks directory path to find the use case
        dir_list = matches[0].split("/")

        # find the last occurrence of framework in the list, then return
        # the element before it in the path, which is the use case
        return next(dir_list[elem - 1] for elem in range(len(dir_list) - 1, -1, -1)
                    if dir_list[elem] == args.framework)

    def get_model_dir(self, benchmark_scripts, use_case):
        """
        Finds the path to the optimized model directory in this repo, if it exists.
        """

        # use the models directory as a default
        intelai_models = os.path.join(benchmark_scripts, os.pardir, "models")

        # find the intelai_optimized model directory
        args = self.args
        optimized_model_dir = os.path.join(
            benchmark_scripts, os.pardir, "models", use_case,
            args.framework, args.model_name)

        # if we find an optimized model, then we will use that path
        if os.path.isdir(optimized_model_dir):
            intelai_models = optimized_model_dir

        return intelai_models

    def get_env_vars(self, benchmark_scripts, use_case, intelai_models):
        """
        Sets up dictionary of standard env vars that are used by start.sh
        """
        # Standard env vars
        args = self.args
        env_var_dict = {
            "DATASET_LOCATION_VOL": args.data_location,
            "CHECKPOINT_DIRECTORY_VOL": args.checkpoint,
            "EXTERNAL_MODELS_SOURCE_DIRECTORY": args.model_source_dir,
            "INTELAI_MODELS": intelai_models,
            "BENCHMARK_SCRIPTS": benchmark_scripts,
            "SOCKET_ID": args.socket_id,
            "MODEL_NAME": args.model_name,
            "MODE": args.mode,
            "PRECISION": args.precision,
            "VERBOSE": args.verbose,
            "BATCH_SIZE": args.batch_size,
            "USE_CASE": use_case,
            "FRAMEWORK": args.framework,
            "NUM_CORES": args.num_cores,
            "NUM_INTER_THREADS": args.num_inter_threads,
            "NUM_INTRA_THREADS": args.num_intra_threads,
            "DATA_NUM_INTER_THREADS": args.data_num_inter_threads,
            "DATA_NUM_INTRA_THREADS": args.data_num_intra_threads,
            "BENCHMARK_ONLY": args.benchmark_only,
            "ACCURACY_ONLY": args.accuracy_only,
            "OUTPUT_RESULTS": args.output_results,
            "DOCKER": str(args.docker_image is not None),
            "PYTHON_EXE": sys.executable if not args.docker_image else "python"
        }

        # Add custom model args as env vars)
        for custom_arg in args.model_args:
            if "=" not in custom_arg:
                raise ValueError("Expected model args in the format "
                                 "`name=value` but received: {}".
                                 format(custom_arg))
            split_arg = custom_arg.split("=")
            split_arg[0] = split_arg[0].replace("-", "_")
            env_var_dict[split_arg[0]] = split_arg[1]

        # Set the default value for NOINSTALL, if it's not explicitly set by the user
        if "NOINSTALL" not in env_var_dict:
            if args.docker_image:
                # For docker, we default to install dependencies
                env_var_dict["NOINSTALL"] = "False"
            else:
                # For bare metal, we default to assume the user has set up their environment
                env_var_dict["NOINSTALL"] = "True"

        return env_var_dict

    def run_bare_metal(self, benchmark_scripts, intelai_models, env_var_dict):
        """
        Runs the model without a container
        """
        # setup volume directories to be the local system directories, since we aren't
        # mounting volumes when running bare metal, but start.sh expects these args
        args = self.args
        mount_benchmark = benchmark_scripts
        mount_external_models_source = args.model_source_dir
        mount_intelai_models = intelai_models
        workspace = os.path.join(benchmark_scripts, "common", args.framework)
        in_graph_path = args.input_graph
        dataset_path = args.data_location
        checkpoint_path = args.checkpoint

        # if using the default output directory, get the full path
        if args.output_dir == "/models/benchmarks/common/tensorflow/logs":
            args.output_dir = os.path.join(workspace, "logs")

        # Add env vars with bare metal settings
        env_var_dict["WORKSPACE"] = workspace
        env_var_dict["MOUNT_BENCHMARK"] = mount_benchmark
        env_var_dict["MOUNT_EXTERNAL_MODELS_SOURCE"] = mount_external_models_source
        env_var_dict["MOUNT_INTELAI_MODELS_SOURCE"] = mount_intelai_models
        env_var_dict["OUTPUT_DIR"] = args.output_dir

        if in_graph_path:
            env_var_dict["IN_GRAPH"] = in_graph_path

        if checkpoint_path:
            env_var_dict["CHECKPOINT_DIRECTORY"] = checkpoint_path

        if dataset_path:
            env_var_dict["DATASET_LOCATION"] = dataset_path

        # Set env vars for bare metal
        for env_var_name in env_var_dict:
            os.environ[env_var_name] = str(env_var_dict[env_var_name])

        # Run the start script
        start_script = os.path.join(workspace, "start.sh")
        self._launch_command(["bash", start_script])

    def run_docker_container(self, benchmark_scripts, intelai_models, env_var_dict):
        """
        Runs a docker container with the specified image and environment
        variables to start running the benchmarking job.
        """
        args = self.args
        mount_benchmark = "/workspace/benchmarks"
        mount_external_models_source = "/workspace/models"
        mount_intelai_models = "/workspace/intelai_models"
        workspace = os.path.join(mount_benchmark, "common", args.framework)

        mount_output_dir = False
        output_dir = os.path.join(workspace, 'logs')
        if args.output_dir != "/models/benchmarks/common/tensorflow/logs":
            # we don't need to mount log dir otherwise since default is workspace folder
            mount_output_dir = True
            output_dir = args.output_dir

        in_graph_dir = os.path.dirname(args.input_graph) if args.input_graph \
            else ""
        in_graph_filename = os.path.basename(args.input_graph) if \
            args.input_graph else ""

        # env vars with docker settings
        env_vars = ["--env", "WORKSPACE={}".format(workspace),
                    "--env", "MOUNT_BENCHMARK={}".format(mount_benchmark),
                    "--env", "MOUNT_EXTERNAL_MODELS_SOURCE={}".format(mount_external_models_source),
                    "--env", "MOUNT_INTELAI_MODELS_SOURCE={}".format(mount_intelai_models),
                    "--env", "OUTPUT_DIR={}".format(output_dir)]

        if args.input_graph:
            env_vars += ["--env", "IN_GRAPH=/in_graph/{}".format(in_graph_filename)]

        if args.data_location:
            env_vars += ["--env", "DATASET_LOCATION=/dataset"]

        if args.checkpoint:
            env_vars += ["--env", "CHECKPOINT_DIRECTORY=/checkpoints"]

        # Add env vars with common settings
        for env_var_name in env_var_dict:
            env_vars += ["--env", "{}={}".format(env_var_name, env_var_dict[env_var_name])]

        # Add proxy to env variables if any set on host
        for environment_proxy_setting in [
            "http_proxy",
            "ftp_proxy",
            "https_proxy",
            "no_proxy",
        ]:
            if not os.environ.get(environment_proxy_setting):
                continue
            env_vars.append("--env")
            env_vars.append("{}={}".format(
                environment_proxy_setting,
                os.environ.get(environment_proxy_setting)
            ))

        volume_mounts = ["--volume", "{}:{}".format(benchmark_scripts, mount_benchmark),
                         "--volume", "{}:{}".format(args.model_source_dir, mount_external_models_source),
                         "--volume", "{}:{}".format(intelai_models, mount_intelai_models)]

        if mount_output_dir:
            volume_mounts.extend([
                "--volume", "{}:{}".format(output_dir, output_dir)])

        if args.data_location:
            volume_mounts.extend([
                "--volume", "{}:{}".format(args.data_location, "/dataset")])

        if args.checkpoint:
            volume_mounts.extend([
                "--volume", "{}:{}".format(args.checkpoint, "/checkpoints")])

        if in_graph_dir:
            volume_mounts.extend([
                "--volume", "{}:{}".format(in_graph_dir, "/in_graph")])

        docker_run_cmd = ["docker", "run"]

        # only use -it when debugging, otherwise we might get TTY error
        if args.debug:
            docker_run_cmd.append("-it")

        docker_run_cmd = docker_run_cmd + env_vars + volume_mounts + [
            "--privileged", "-u", "root:root", "-w",
            workspace, args.docker_image, "/bin/bash"]

        if not args.debug:
            docker_run_cmd.append("start.sh")

        if args.verbose:
            print("Docker run command:\n{}".format(docker_run_cmd))

        self._launch_command(docker_run_cmd)

    def _launch_command(self, run_cmd):
        """runs command that runs the start script in a container or on bare metal and exits on ctrl c"""
        p = subprocess.Popen(run_cmd, preexec_fn=os.setsid)
        try:
            p.communicate()
        except KeyboardInterrupt:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)


if __name__ == "__main__":
    util = LaunchBenchmark()
    util.main()
