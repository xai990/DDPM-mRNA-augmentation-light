#!/bin/bash

#SBATCH --job-name={{JOB_NAME}}       # Set the job name
#SBATCH --nodes 1
#SBATCH --tasks-per-node 1
#SBATCH --cpus-per-task 4
#SBATCH --mem 32gb
#SBATCH --gpus a100:1
#SBATCH --time 48:00:00

# set -e 

# This should be the directory where you cloned the DDPM-mRNA-augmentation repository
DDPM_DIR="/scratch/xai/DDPM-mRNA-augmentation-light"



# Create conda environment from instructions in DDPM-mRNA-augmentation readme
module purge
module load anaconda3/2023.09-0
source activate DDIM 

# Move to the python package directory 
cd ${DDPM_DIR}

# config file path 
CONFIG_PATH={{CONFIG_PATH}}
RESULT_PATH={{RESULT_PATH}}
LOG_PATH={{LOG_PATH}}
SAMPLE_PATH={{SAMPLE_PATH}}
# Define the pattern to search for .egg-info directories
egg_info_pattern="*.egg-info"

if find . -maxdepth 1 -type d -name "$egg_info_pattern" | grep -q .; then
    echo "The .egg-info directory exists. Skipping 'pip install -e .'"
else
    # install and build the package environment 
    pip install -e .
fi


python scripts/plot.py --config $CONFIG_PATH --dir $LOG_PATH --sample_path $SAMPLE_PATH --dir_out $RESULT_PATH
