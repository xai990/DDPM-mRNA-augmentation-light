# Modified from OpenAI's diffusion repos
#     GLIDE: https://github.com/openai/glide-text2im/blob/main/glide_text2im/gaussian_diffusion.py
#     ADM:   https://github.com/openai/guided-diffusion/blob/main/guided_diffusion
#     IDDPM: https://github.com/openai/improved-diffusion/blob/main/improved_diffusion/gaussian_diffusion.py
#     DiT: https://github.com/facebookresearch/DiT/blob/main/diffusion/gaussian_diffusion.py

import torch as th 
import numpy as np 
from tqdm import tqdm 
import torch.nn.functional as F
from . import logger 
from .diffusion_util import mean_flat, discretized_gaussian_log_likelihood, normal_kl
import math 
import enum

"""
log:
* add loss type to model 
* consider kl and discrete kl 
"""

def betas_for_alpha_bar(num_diffusion_timesteps, alpha_bar, max_beta=0.999):
    """
    Create a beta schedule that discretizes the given alpha_t_bar function,
    which defines the cumulative product of (1-beta) over time from t = [0,1].

    :param num_diffusion_timesteps: the number of betas to produce.
    :param alpha_bar: a lambda that takes an argument t from 0 to 1 and
                      produces the cumulative product of (1-beta) up to that
                      part of the diffusion process.
    :param max_beta: the maximum beta to use; use values lower than 1 to
                     prevent singularities.
    """
    betas = []
    for i in range(num_diffusion_timesteps):
        t1 = i / num_diffusion_timesteps
        t2 = (i + 1) / num_diffusion_timesteps
        betas.append(min(1 - alpha_bar(t2) / alpha_bar(t1), max_beta))
    return np.array(betas)


def get_named_beta_schedule(
    schedule_name, 
    num_diffusion_timesteps,
    linear_start,
    linear_end,
    cosine_s=8e-3):
    """
    Get a pre-defined beta schedule for the given condition.

    The beta schedule library consists of beta schedules which remain similar 
    in the limit of num_diffusion_timesteps.
    Beta schedules may be added, but should not be removed or changed once
    they are committed to maintain backwards compatibility. 
    """
    if schedule_name == "linear":
        betas = (
            np.linspace(linear_start ** 0.5, linear_end ** 0.5, num_diffusion_timesteps, dtype=np.float64) ** 2
        )
    elif schedule_name == "cosine":
        return betas_for_alpha_bar(
            num_diffusion_timesteps,
            lambda t: math.cos((t + 0.008) / 1.008 * math.pi / 2) ** 2,
        )
    elif schedule_name == "sigmoid":
        betas = th.linspace(linear_start, linear_end,num_diffusion_timesteps)
        betas = th.sigmoid(betas)*(0.5e-2 - 1e-5)+1e-5
        betas = betas.numpy()
    else:
        raise NotImplementedError(f"unknown beta schedule: {schedule_name}")

    return betas


def betas_for_alpha_bar(num_diffusion_timesteps, alpha_bar, max_beta=0.999):
    """
    Create a beta schedule that discretizes the given alpha_t_bar function,
    which defines the cumulative product of (1-beta) over time from t = [0,1].

    :param num_diffusion_timesteps: the number of betas to produce.
    :param alpha_bar: a lambda that takes an argument t from 0 to 1 and
                      produces the cumulative product of (1-beta) up to that
                      part of the diffusion process.
    :param max_beta: the maximum beta to use; use values lower than 1 to
                     prevent singularities.
    """
    betas = []
    for i in range(num_diffusion_timesteps):
        t1 = i / num_diffusion_timesteps
        t2 = (i + 1) / num_diffusion_timesteps
        betas.append(min(1 - alpha_bar(t2) / alpha_bar(t1), max_beta))
    return np.array(betas)



class LossType(enum.Enum):
    MSE = enum.auto()
    KL = enum.auto()

    def is_vb(self):
        return self == LossType.KL


class ModelMeanType(enum.Enum):
    """
    which type of output the model predicts
    """
    EPSILON = enum.auto()


class ModelVarType(enum.Enum):
    """
    What is used as the model's output variance.

    The LEARNED_RANGE option has been added to allow the model to predict
    values between FIXED_SMALL and FIXED_LARGE, making its job easier.
    """

    LEARNED = enum.auto()
    FIXED = enum.auto()


class DenoiseDiffusion():    
    """
    Utilities for training and sampling diffusion model.

    """

    def __init__(
        self,
        *,
        betas,
        model_mean_type,
        model_var_type,
        loss_type,
        log_every_t,
    ):
        super().__init__()
        
        self.betas = betas 
        self.loss_type = loss_type
        self.model_mean_type = model_mean_type 
        self.model_var_type = model_var_type 
        assert len(betas.shape) == 1, "beta must be 1-D"
        assert (betas > 0).all() and (betas <= 1).all()

        self.num_timesteps = int (betas.shape[0])
        self.alphas = 1. - self.betas
        self.alphas_cumprod = np.cumprod(self.alphas,axis=0)
        self.alphas_cumprod_prev = np.append(1., self.alphas_cumprod[:-1])
        assert self.alphas_cumprod_prev.shape == (self.num_timesteps,)

        # calculations for diffusion q(x_t | x_{t-1}) and others 
        self.sqrt_alphas_cumprod = np.sqrt(self.alphas_cumprod)
        self.sqrt_one_minus_alphas_cumprod = np.sqrt(1. - self.alphas_cumprod)
        self.sqrt_recip_alphas_cumprod = np.sqrt(1. / self.alphas_cumprod) 
        self.sqrt_recipm1_alphas_cumprod = np.sqrt( 1. / self.alphas_cumprod - 1)
        # calculations for posterior  q(x_{t-1} | x_t, x_0)
        self.posterior_mean_coef1 = (
            betas * np.sqrt(self.alphas_cumprod_prev)
            /( 1. - self.alphas_cumprod)
        )
        self.posterior_mean_coef2 = (
            np.sqrt(self.alphas) * ( 1. - self.alphas_cumprod_prev)
            / ( 1. - self.alphas_cumprod)
        )
        self.posterior_variance = (
            (1 - self.alphas_cumprod_prev) * betas /
            (1 - self.alphas_cumprod)
        )
        self.posterior_log_variance_clipped = np.log(np.maximum(self.posterior_variance, 1e-20))
        self.log_every_t = log_every_t


    def q_mean_variance(self,x_start,t):
        """
        Get the distribution q(x_t | x_0).
        
        :param x_start: the [N x C x ...] tensor of noiseless input.
        :param t: the number of diffusion steps (minus 1). Here, 0 means step one.
        :return: A tuple (mean, variance, log_variance), all of x_start's shape.
        """

        mean = (
            _extract_into_tensor(self.sqrt_alphas_cumprod,t,x_start.shape) * x_start 
        )
        variance = (
            _extract_into_tensor(self.sqrt_one_minus_alphas_cumprod, t, x_start.shape)
        )
        return mean, variance 


    def q_sample(self,x_start,t,noise=None):
        """
        Diffusion the data for a given number of diffusion steps.
        
        :param x_start: the initial data batch.
        :param t: the number of diffusion steps (minus 1).
        :param noise: if specified, the split-out normal noise.
        :return A nosy version of x_start.
        """
        if noise is None:
            noise = th.randn_like(x_start)

        assert noise.shape == x_start.shape
        mean, var = self.q_mean_variance(x_start,t)
        return mean + var * noise 


    def _predict_start_from_noise(self, x_t, t, noise):
        return (
            _extract_into_tensor(self.sqrt_recip_alphas_cumprod, t, x_t.shape) * x_t -
            _extract_into_tensor(self.sqrt_recipm1_alphas_cumprod, t, x_t.shape) * noise
        )


    def q_posterior(self, x_start, x_t, t):
        """
        Compute the mean and variance of the diffusion posterior:
            q(x_{t-1} | x_t, x_0)
        """

        assert x_start.shape == x_t.shape
        posterior_mean = (
            _extract_into_tensor(self.posterior_mean_coef1, t, x_t.shape) * x_start +
            _extract_into_tensor(self.posterior_mean_coef2, t, x_t.shape) * x_t
        )
        posterior_variance = _extract_into_tensor(self.posterior_variance, t, x_t.shape)
        posterior_log_variance_clipped = _extract_into_tensor(self.posterior_log_variance_clipped,t,x_t.shape)
        return posterior_mean, posterior_variance, posterior_log_variance_clipped

    
    @th.no_grad()
    def p_mean_variance(
        self, model, x, t, clip_denoised=True, model_kwargs=None
    ):
        """
        Apply the model to get p(x_{t-1}| x_t), as well as a prediction of 
        the initial x, x_0.

        :param model: the model, which takes a signal and a batch of timesteps
                      as input.
        :param x: the [N x C x ...] tensor at time t.
        :param t: a 1-D Tensor of timesteps.
        :param clop_denoised: if True, clip the denoised signal into [-1,1].
        :param model_kwargs: if not None, a dict of extra keyword arguments to 
                             pass to the model. This can be used for conditioning.
        :return: a dict with the following keys:
                - 'mean': the model mean output.
                - 'variance': the model variance output.
                - 'log_variance': the log of 'variance'. log transform increase the stability and easier to optimize
        """

        if model_kwargs is None:
            model_kwargs = {}
        
        B, F  = x.shape
        assert t.shape == (B,)
        model_output = model(x,t,**model_kwargs)
        
        if self.model_var_type == ModelVarType.LEARNED:
            assert model_output.shape == (B,F*2)
            model_output, model_var_values = th.split(model_output, F, dim=1)
            min_log = _extract_into_tensor(self.posterior_log_variance_clipped, t, x.shape)
            max_log = _extract_into_tensor(np.log(self.betas), t, x.shape)
            frac = (model_var_values + 1) / 2
            model_log_variance = frac * max_log + (1 - frac) * min_log 
            model_variance = th.exp(model_log_variance)
        else:
            # fixed large (log var without clip)
            model_variance, model_log_variance = np.append(self.posterior_variance[1], self.betas[1:]),np.log(np.append(self.posterior_variance[1], self.betas[1:]))
        
        posterior_variance = _extract_into_tensor(model_variance, t, x.shape)
        posterior_log_variance = _extract_into_tensor(model_log_variance, t, x.shape)

        if self.model_mean_type == ModelMeanType.EPSILON:
            x_recon = self._predict_start_from_noise(x, t=t,noise=model_output)
        if clip_denoised:
            x_recon.clamp_(-1. , 1.)
        
        model_mean, _, _ = self.q_posterior(x_start=x_recon, x_t=x, t=t)
        
        return {
            "model_mean": model_mean,
            "posterior_variance": posterior_variance,
            "posterior_log_variance": posterior_log_variance,
            "pred_xstart": x_recon,
        }

    @th.no_grad()
    def p_sample(
        self, model, x, t, clip_denoised=True, model_kwargs=None
    ):
        """
        Sample x_{t-1} from the model at the given timestep.

        :param model: the model to sample from.
        :param x: the current tensor at x_{t-1}.
        :param t: the value of t, starting at 0 for the first diffusion step.
        :param clip_denoised: if True, clip the x_start prediction to [-1, 1].
        """

        out = self.p_mean_variance(model,x,t, clip_denoised=clip_denoised, model_kwargs=model_kwargs)
        noise = th.randn_like(x)
        nonzero_mask = (
            (t != 0).float().view(-1, *([1] * (len(x.shape) - 1)))
        )  # no noise when t == 0
        sample = out["model_mean"] + nonzero_mask * th.exp(0.5 * out["posterior_log_variance"]) * noise
        return {"sample": sample, "pred_xstart":out["pred_xstart"]}


    @th.no_grad()
    def p_sample_loop(
        self, model,shape, return_intermediates=False, model_kwargs=None,
    ):

        device = next(model.parameters()).device
        seq = th.randn(shape, device=device)
        intermediates = [seq]
        # tqdm is used to display progress bars in loops or iterable processes
        for i in tqdm(reversed(range(0,self.num_timesteps)),
                      desc='Sampling t',
                      total=self.num_timesteps,
        ):
            t = th.tensor([i] * shape[0], device = device)
            out = self.p_sample(model, seq, t, model_kwargs=model_kwargs)
            if i % self.log_every_t == 0:                
                intermediates.append(out["sample"])
            seq = out["sample"]
        final = out["sample"]
        if return_intermediates:
            return final, intermediates 
        return final 


    def _vb_terms_bpd(
        self, model, x_start, x_t, t, clip_denoised=True, model_kwargs=None
    ):
        """
        Get a term for the variational lower-bound.
        :return: a dict with the following keys:
            - 'output': a shape [N] tensor of NLLs or KLs.
            - 'pred_xstart': the x_0 predictions.
        """
        true_mean, _, true_log_variance_clipped = self.q_posterior_mean_variance(
            x_start=x_start, x_t=x_t, t=t
        )
        out = self.p_mean_variance(
            model, x_t, t, model_kwargs=model_kwargs
        )
        kl = normal_kl(
            true_mean, true_log_variance_clipped, out["model_mean"], out["posterior_log_variance"]
        )
        kl = mean_flat(kl)/np.log(2.0)

        decoder_nll = -discretized_gaussian_log_likelihood(
            x_start, means=out["model_mean"], log_scales=0.5 * out["posterior_log_variance"]
        )
        assert decoder_nll.shape == x_start.shape
        decoder_nll = mean_flat(decoder_nll) / np.log(2.0)
        output = th.where((t==0), decodeer_nll,kl)
        return {"output": output, "pred_xstart": out["pred_xstart"]}


    def loss(self, model, x_start, t, noise=None, model_kwargs=None):
        """
        Calculate the loss
        """
        if model_kwargs is None:
            model_kwargs = {}
        terms = {}
        noise = th.randn_like(x_start, device = x_start.device)
        x_t = self.q_sample(x_start = x_start, t=t,noise=noise)
        model_out = model(x_t, t, **model_kwargs)

        if self.parameterization == "eps":
            target = noise 
        else:
            raise NotImplementedError(f"ParameteriZation {self.parameterization} not yet supported")
        
        if self.loss_type == LossType.MSE:
            target = {
                ModelMeanType.EPSILON:noise,
            }[self.model_mean_type]
            assert model_output.shape == target.shape == x_start.shape
            # the take the mean on each dimension [N x C x F], dim = [1,2]
            terms["mse"] = mean_flat((target-model_output) ** 2)
        
        if "vb" in terms:
            terms["loss"] = terms["mse"] + terms["vb"]
        else:
            terms["loss"] = terms["mse"]
        return terms
    

def _extract_into_tensor(arr, timesteps, broadcast_shape):
    """
    Extract values from a 1-D numpy array for a batch of indices.

    :param arr: the 1-D numpy array.
    :param timesteps: a tensor of indices into the array to extract. 
    :param broadcast_shape: a larger shape of K dimensions with the batch 
                            dimension equal to the length of timesteps.
    :return: a tensor of shape [batch_size, 1, ...] where the shape has K dims.
    """
    res = th.from_numpy(arr).to(device=timesteps.device)[timesteps].float()
    while len(res.shape) < len(broadcast_shape):
        res = res[...,None]
    return res.expand(broadcast_shape)
    

