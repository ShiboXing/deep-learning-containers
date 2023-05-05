# adapted from https://github.com/NVIDIA/apex/blob/master/tests/distributed/DDP/ddp_race_condition_test.py

import torch
import torch.distributed as dist
from torch.nn import Parameter
from torch.nn import Module
from apex.parallel import DistributedDataParallel as DDP
import argparse
import os


parser = argparse.ArgumentParser(description="allreduce hook example")
parser.add_argument("--local_rank", default=0, type=int)
args = parser.parse_args()

dist.init_process_group(backend="nccl", init_method="env://")
args.world_size = dist.get_world_size()
args.local_rank = dist.get_rank()
args.gpu = args.local_rank % torch.cuda.device_count()
torch.cuda.set_device(args.gpu)
print("assigned dist rank data", args.local_rank, args.world_size)

torch.set_printoptions(precision=10)
torch.manual_seed(args.local_rank)


class Model(Module):
    def __init__(self):
        super(Model, self).__init__()
        self.a = Parameter(torch.cuda.FloatTensor(4096 * 4096).fill_(1.0))
        self.b = Parameter(torch.cuda.FloatTensor(4096 * 4096).fill_(2.0))

    def forward(self, input):
        return (input * self.a) * self.b


model = Model()
# model = DDP(model, message_size=1, gradient_predivide_factor=8.0)
# model = DDP(model, delay_allreduce=True)
# model = DDP(model, message_size=1, allreduce_trigger_params=[model.b])
model = DDP(model, message_size=1, allreduce_trigger_params=[model.b], num_allreduce_streams=3)

x = torch.cuda.FloatTensor(4096 * 4096)

model_a_passed = True
model_b_passed = True
torch.cuda.cudart().cudaProfilerStart()
for i in range(10):
    x.fill_(i + args.local_rank)  # fill x with new values every iteration for sanity
    model.zero_grad()
    out = model(x)
    loss = out.sum()
    # torch.cuda.nvtx.range_push("backward")
    loss.backward()
    # torch.cuda.nvtx.range_pop()

    # torch.cuda.nvtx.range_push("synchronize() + info")
    # torch.cuda.synchronize()
    print("i = {}".format(i))

    def info(name, param, val):
        expected = val * 4096 * 4096 * (2.0 * i + 1) / 2.0
        actual = param.grad.data.sum().item()
        print(
            name
            + ": grad.data_ptr() = {}, expected sum {}, got {}".format(
                param.grad.data_ptr(), expected, actual
            )
        )
        return expected == actual

    model_a_passed = info("model.a", model.module.a, 2.0)
    model_b_passed = info("model.b", model.module.b, 1.0)
    if not model_a_passed or not model_b_passed:
        break
    # torch.cuda.nvtx.range_pop()
torch.cuda.cudart().cudaProfilerStop()

assert model_a_passed and model_b_passed, (
    "Failure: model.a success: {model_a_passed}, model.b success: {model_b_passed}"
)
print("Success on model.a and model.b")
