#!/bin/bash

#SBATCH --job-name=random_gene       # Set the job name
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
CONFIG_PATH="configs/mrna_16.yaml"
LOG_PATH="log/random"

# Define the pattern to search for .egg-info directories
egg_info_pattern="*.egg-info"

if find . -maxdepth 1 -type d -name "$egg_info_pattern" | grep -q .; then
    echo "The .egg-info directory exists. Skipping 'pip install -e .'"
else
    # install and build the package environment 
    pip install -e .
fi


python scripts/perturb.py --config $CONFIG_PATH --dir $LOG_PATH --vaild
