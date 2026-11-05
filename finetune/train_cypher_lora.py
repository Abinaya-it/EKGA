"""FR-4b: LoRA-tune a 7-8B instruct model (QLoRA) for text->Cypher. Needs transformers, peft, trl, bitsandbytes (offline wheels) + a local model dir.
BASE_LLM=/models/Qwen2.5-7B-Instruct python -m finetune.train_cypher_lora   -> models/cypher-lora (merge/convert to GGUF for Ollama)"""
import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from config import DATA
from datasets import Dataset
from peft import LoraConfig
from trl import SFTTrainer, SFTConfig
from agents.cypher_agent import SCHEMA

rows = [json.loads(l) for l in open(DATA / "finetune" / "cypher_sft.jsonl")]
ds = Dataset.from_list([{"text": f"{SCHEMA}\nQuestion: {r['question']}\nAnswer: " + json.dumps({"cypher": r["cypher"], "params": r["params"]})} for r in rows])
cfg = SFTConfig(output_dir=os.getenv("OUT", "models/cypher-lora"), num_train_epochs=3, per_device_train_batch_size=2, gradient_accumulation_steps=8,
                learning_rate=2e-4, logging_steps=5, save_strategy="epoch", bf16=True, max_length=1024, dataset_text_field="text")
SFTTrainer(model=os.environ["BASE_LLM"], train_dataset=ds, args=cfg,
           peft_config=LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, target_modules="all-linear", task_type="CAUSAL_LM")).train()
