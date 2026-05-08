from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template
from mlx_vlm.utils import load_config

model, processor = load("deadbydawn101/gemma-4-E2B-Heretic-Uncensored-mlx-4bit")
config = load_config("deadbydawn101/gemma-4-E2B-Heretic-Uncensored-mlx-4bit")

image = ["Unknown.png"]
prompt = "이미지 안에 있는 글을 반드시 한국어로 변역해 발음 말고 변역된 말 외에는 아무말도 하지말아야하고 변역된말의 위치도 알려줘."

formatted_prompt = apply_chat_template(
    processor, config, prompt, num_images=1
)

output = generate(model, processor, formatted_prompt, image
)
print(output)