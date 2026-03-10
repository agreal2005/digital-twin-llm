from llama_cpp import Llama
import os
import sys

print(f"Python version: {sys.version}")
print(f"Current directory: {os.getcwd()}")

# Path to your GGUF model
model_path = "/home/agreal2016/btp/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf"

# Check if model exists
if not os.path.exists(model_path):
    print(f"❌ Model not found at {model_path}")
    if os.path.exists("models"):
        print(f"Files in models/: {os.listdir('models/')}")
    else:
        print("models/ directory not found")
    sys.exit(1)

print(f"✅ Model found at {model_path}")
print(f"📊 File size: {os.path.getsize(model_path) / (1024*1024):.2f} MB")

try:
    print("\n🔄 Loading model...")
    llm = Llama(
        model_path=model_path,
        n_ctx=4096,
        n_threads=4,
        verbose=False
    )
    print("✅ Model loaded successfully!")
    
    prompt = "Explain what a network digital twin is in simple terms."
    print(f"\n🔄 Generating response for: {prompt[:50]}...")
    
    response = llm(prompt, max_tokens=150)
    
    print("\n✅ Response:")
    print("-" * 50)
    print(response['choices'][0]['text'].strip())
    print("-" * 50)
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()