import argparse 
import os 
import matplotlib.pyplot as plt
from . import gaussian_diffusion as gd 
from .respace import SpacedDiffusion, space_timesteps
import torch as th 
from .mlp import GPT
from . import logger 
import umap.plot
import numpy as np 
import plotly.graph_objects as go

NUM_CLASSES = 2

def model_and_diffusion_defaults():
    """
    Defaults for model and diffusion model
    """
    return {
        "model":{
            "feature_size": 2,
            "patch_size": 2,
            "dropout": 0.0,
            "class_cond": False,
            "n_embd": 768,
            "n_head": 4,
            "n_layer": 4,
        },
        "diffusion":{
            "diffusion_steps": 4000,
            "noise_schedule": "linear",
            "linear_start": 0.0015,
            "linear_end": 0.0195,
            "log_every_t": 10,
            "schedule_sampler": "uniform",
            "learn_sigma": True,
            "rescale_timesteps": True, 
            "timestep_respacing":"",
        }
    }


def create_model_and_diffusion(
    *,
    feature_size,
    class_cond,
    n_embd,
    n_head,
    dropout,
    diffusion_steps,
    noise_schedule,
    linear_start,
    linear_end,
    log_every_t,
    n_layer,
    patch_size,
    learn_sigma,
    rescale_timesteps,
    timestep_respacing,
    **kwargs,
):
    model = create_model(
        feature_size = feature_size,
        n_embd = n_embd,
        n_head = n_head,
        dropout = dropout,
        class_cond = class_cond,
        n_layer = n_layer,
        patch_size = patch_size,
        learn_sigma = learn_sigma,
    )
    diffusion = create_diffusion(
        steps= diffusion_steps,
        noise_schedule = noise_schedule,
        linear_start= linear_start,
        linear_end = linear_end,
        log_every_t = log_every_t,
        learn_sigma = learn_sigma,
        rescale_timesteps = rescale_timesteps,
        timestep_respacing=timestep_respacing,
    )
    return model, diffusion


def create_model(
        *,
        feature_size,
        n_embd,
        class_cond,
        patch_size,
        n_head,
        n_layer = 4,
        dropout = 0.0,
        learn_sigma = True,
    ):
    
    return GPT(
        gene_feature=feature_size,
        n_embd=n_embd,
        n_head=n_head,
        dropout=dropout,
        n_layer = n_layer,
        patch_size =patch_size,
        num_classes=(NUM_CLASSES if class_cond else None),
        learn_sigma = learn_sigma,
    )

def create_diffusion(
    *,
    steps = 1000,
    noise_schedule = "linear",
    linear_start = 0.0015,
    linear_end = 0.0195,
    log_every_t = 10,
    learn_sigma = True, 
    rescale_timesteps = False,
    timestep_respacing = "",
):
    betas = gd.get_named_beta_schedule(noise_schedule, steps, linear_start,linear_end)
    # logger.debug(f"The betas is {betas} -- script")
    loss_type = gd.LossType.MSE
    if timestep_respacing is None or timestep_respacing == "":
        timestep_respacing = [steps]
    # return DenoiseDiffusion(
    #     betas = betas,
    #     log_every_t = log_every_t,
    #     loss_type = loss_type,
    #     model_mean_type = gd.ModelMeanType.EPSILON,
    #     model_var_type = gd.ModelVarType.LEARNED if learn_sigma else gd.ModelVarType.FIXED
    # )
    return SpacedDiffusion(
        use_timesteps=space_timesteps(steps, timestep_respacing),
        betas=betas,
        model_mean_type=gd.ModelMeanType.EPSILON,
        model_var_type=gd.ModelVarType.LEARNED if learn_sigma else gd.ModelVarType.FIXED,
        log_every_t = log_every_t,
        loss_type = loss_type,
        rescale_timesteps = rescale_timesteps,
    )


def zero_grad(model_params):
    for param in model_params:
        if param.grad is not None:
            param.grad.detach_()
            param.grad.zero_()



def showdata(dataset, 
            dir="results", 
            schedule_plot = "forward", 
            diffusion=None,
            num_steps=1000,
            num_shows=20,
            cols = 10,
            synthesis_data = None,
    ):
    # dir to save the plot image
    assert isinstance(dir,str)
    os.makedirs(dir,exist_ok=True)
    reducer = umap.UMAP(n_neighbors=60, min_dist=0.3, n_components=2, random_state=41)
    added_labels = set()
    if schedule_plot == "forward": 
        colors = ['blue','orange']
        labels = ['normal','tumor']
        assert diffusion is not None
        rows = num_shows // cols
        fig,axes = plt.subplots(rows,cols,figsize=(28,3))
        plt.rc('text',color='black')
        data , y = dataset[:][0], dataset[:][1]['y']
        data = th.from_numpy(data).float()
        for i in range(num_shows):
            j = i // cols
            k = i % cols
            t = th.full((data.shape[0],),i*num_steps//num_shows)
            x_i = diffusion.q_sample(data,t)
            q_i = reducer.fit_transform(x_i)
            added_labels.clear()
            for ele in y:
                index_mask = (ele==y)
                if np.any(index_mask):
                    label = labels[ele] if ele not in added_labels else None
                    axes[j,k].scatter(q_i[index_mask,0],
                                q_i[index_mask,1],
                                label = label, 
                                color=colors[ele],
                                edgecolor='white')
                    if label:
                        added_labels.add(ele)
            axes[j,k].set_axis_off()
            axes[j,k].set_title('$q(\mathbf{x}_{'+str(i*num_steps//num_shows)+'})$')
        handles, labels = axes[0, 0].get_legend_handles_labels()  # Get handles and labels from any one subplot
        fig.legend(handles, labels, loc='upper left', ncol=3)
        plt.savefig(f"{dir}/dataset-forward_{data.shape[-1]}.png")
        plt.close()
    elif schedule_plot == "origin":
        # fig,ax = plt.subplots()      
        data , y = dataset[:][0], dataset[:][1]['y']
        # Define the range of parameters you want to explore
        n_neighbors_options = [15, 30, 60, 90, 120, 150, 180]
        min_dist_options = [0.1, 0.3, 0.6, 0.9]
        color_map = ['blue','orange']
        
        labels = ['normal','tumor']
        # Setup the subplot grid
        fig, axes = plt.subplots(nrows=len(n_neighbors_options), ncols=len(min_dist_options), figsize=(15, 15))
        for i, n_neighbors in enumerate(n_neighbors_options):
            for j, min_dist in enumerate(min_dist_options):
                # Create UMAP model
                reducer = umap.UMAP(n_neighbors=n_neighbors, min_dist=min_dist, random_state=41)
                embedding = reducer.fit_transform(data)
                # Plot
                ax = axes[i,j]  # Get a specific subplot axis
                added_labels.clear()
                for ele in y:
                    index_mask = (ele==y)
                    if np.any(index_mask):
                        label = labels[ele] if labels[ele] not in added_labels else None
                        ax.scatter(embedding[index_mask,0],
                                    embedding[index_mask,1],
                                    label = label, 
                                    color=color_map[ele],
                                    edgecolor='white')
                        if label:
                            added_labels.add(label)
                # scatter = ax.scatter(embedding[:, 0], embedding[:, 1], c=y, cmap='viridis', s=5)
                # ax.set_title(f'n_neighbors={n_neighbors}, min_dist={min_dist}')
                if i == 0:
                    ax.set_xlabel(f'{min_dist}')
                    ax.xaxis.set_label_position('top')
                if j == 0:
                    ax.set_ylabel(f'{n_neighbors}')
                ax.axes.xaxis.set_ticklabels([])
                ax.axes.yaxis.set_ticklabels([])
                # ax.axis('off')
        handles, labels = axes[0, 0].get_legend_handles_labels()
        fig.legend(handles, labels, loc='upper left', ncol=3)
        fig.text(0.5, 1,'min_dist',ha='center',)
        fig.text(0.0, 0.5,'n_neighbors', va='center', rotation='vertical')
        plt.tight_layout()
        plt.subplots_adjust(wspace=0., hspace=0.)
        
        """
        q_i = reducer.fit_transform(data)
        for ele in y:
            index_mask = (ele==y)
            if np.any(index_mask):
                ax.scatter(q_i[index_mask,0],
                            q_i[index_mask,1],
                            label = ele, 
                            color=colors[ele],
                            edgecolor='white')
        ax.axis('off')
        """
        plt.savefig(f"{dir}/dataset_{data.shape[-1]}.png")
        plt.close()
    elif schedule_plot == "reverse":
        data_r , y_r = dataset[:][0], dataset[:][1]['y']
        data_f, y_f = synthesis_data["arr_0"], synthesis_data["arr_1"]
        # stack the data together for overarching patterns 
        data_merged = np.vstack([data_r,data_f])
        
        # create the umap parameters sweep plot 
        n_neighbors_options = [15, 30, 60, 90, 120, 150, 180]
        min_dist_options = [0.1, 0.3, 0.6, 0.9]
        color_map = ['blue','orange','cyan','blueviolet']
        labels = ['real_normal','real_tumor','fake_normal', 'fake_turmor']
        # Setup the subplot grid
        fig, axes = plt.subplots(nrows=len(n_neighbors_options), ncols=len(min_dist_options), figsize=(15, 15))
        for i, n_neighbors in enumerate(n_neighbors_options):
            for j, min_dist in enumerate(min_dist_options):
                # Create UMAP model
                reducer = umap.UMAP(n_neighbors=n_neighbors, min_dist=min_dist, random_state=41)
                q_i = reducer.fit_transform(data_merged)
                q_r = q_i[:len(y_r)]
                q_f = q_i[len(y_r):]
                # Plot
                ax = axes[i, j]  # Get a specific subplot axis
                added_labels.clear()
                for ele in y_r:
                    index_mask = (ele==y_r)
                    if np.any(index_mask):
                        label = labels[ele] if labels[ele] not in added_labels else None
                        ax.scatter(q_r[index_mask,0],
                                    q_r[index_mask,1],
                                    label = label, 
                                    color=color_map[ele],
                                    edgecolor='white')
                        if label:
                            added_labels.add(label)

                for ele in y_f:
                    index_mask = (ele==y_f)
                    if np.any(index_mask):
                        label = labels[ele+2] if labels[ele+2] not in added_labels else None
                        ax.scatter(q_f[index_mask,0],
                                    q_f[index_mask,1],
                                    label = label, 
                                    color=color_map[ele+2],
                                    edgecolor='white')
                        if label:
                            added_labels.add(label)

                # scatter = ax.scatter(embedding[:, 0], embedding[:, 1], c=y, cmap='viridis', s=5)
                # ax.set_title(f'n_neighbors={n_neighbors}, min_dist={min_dist}')
                if i == 0:
                    ax.set_xlabel(f'{min_dist}')
                    ax.xaxis.set_label_position('top')
                if j == 0:
                    ax.set_ylabel(f'{n_neighbors}')
                ax.axes.xaxis.set_ticklabels([])
                ax.axes.yaxis.set_ticklabels([])
                # ax.axis('off')
        handles, labels = axes[0, 0].get_legend_handles_labels()
        fig.legend(handles, labels, loc='upper left', ncol=3)
        fig.text(0.5, 1,'min_dist',ha='center',)
        fig.text(0.0, 0.5,'n_neighbors', va='center', rotation='vertical')
        plt.tight_layout()
        plt.subplots_adjust(wspace=0., hspace=0.)
        # plt.show()
        plt.legend(loc="upper left")
        plt.savefig(f"{dir}/UMAP_plot_realvsfake_{data_r.shape[-1]}.png")
        plt.close()
        """
        # Create a Plotly figure
        fig = go.Figure()
        # plot the synthesis data
        for ele in np.unique(y_f):
            index_mask = (ele==y_f)
            if np.any(index_mask):
                # Add the first scatter plot to the figure
                label = f'Synthesis {ele}'
                fig.add_trace(go.Scatter(x=q_f[index_mask,0], y=q_f[index_mask,1],
                                        mode='markers', 
                                        name=label,textposition='top center')
                                        )

        # Add the original data scatter plot to the figure
        for ele in np.unique(y_r):
            index_mask = (ele==y_r)
            if np.any(index_mask):
                label = f'Real {ele}'
                fig.add_trace(go.Scatter(x=q_r[index_mask,0], y=q_r[index_mask,1],
                                        mode='markers', 
                                        name=label,textposition='top center')
                                        )
                        

        # Update the layout
        fig.update_layout(title='UMAP Plots with Real mRNA data and synthetic mRNA data',
                        xaxis_title='X Axis',
                        yaxis_title='Y Axis',
                        legend_title='Datasets')

        # Show the figure
        fig.write_html(f"{dir}/UMAP_plot_realvsfake_{data_r.shape[-1]}.html")
        """
    else:
        raise NotImplementedError(f"unknown schedule plot:{schedule_plot}")



def find_model(model_name):
    assert os.path.isfile(model_name), f'Could not find model checkpoint at {model_name}'
    checkpoint = th.load(model_name)
    return checkpoint 