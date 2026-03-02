import ollama
import time

def run_ollama_test():
    """
    Performs a direct test of the ollama.embeddings function.
    """
    print("--- Starting Ollama Embeddings Test ---")
    model = 'nomic-embed-text'
    prompt = 'This is a test sentence.'

    try:
        print(f"Attempting to generate embedding for '{prompt}' using model '{model}'...")
        start_time = time.time()
        
        # This is the function call that is likely hanging in the app
        response = ollama.embeddings(model=model, prompt=prompt)
        
        end_time = time.time()
        duration = end_time - start_time
        
        if response.get('embedding'):
            print(f"[PASS] Successfully generated embedding in {duration:.2f} seconds.")
            print(f"   Embedding vector starts with: {str(response['embedding'][:5])}...")
        else:
            print("[FAIL] The call succeeded but did not return an embedding.")
            print(f"   Full response: {response}")

    except Exception as e:
        print(f"[FAIL] The ollama.embeddings call failed with an exception.")
        print(f"   Error: {e}")

if __name__ == "__main__":
    run_ollama_test()
