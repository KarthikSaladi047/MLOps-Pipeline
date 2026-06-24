import os
import requests
import gradio as gr

# Fallback to V2 endpoint if environment variable isn't injected/updated in K8s
KSERVE_URL = os.getenv(
    "KSERVE_INFERENCE_URL",
    "http://iris-classifier-predictor.ml-serving.svc.cluster.local/v2/models/iris-classifier/infer"
)

# Target class mapping for the Iris dataset
IRIS_CLASSES = {
    0: "Setosa",
    1: "Versicolor",
    2: "Virginica"
}

def predict_iris(sepal_l, sepal_w, petal_l, petal_w):
    # Strictly formatted V2 Inference Protocol payload
    payload = {
        "parameters": {
            "content_type": "np"  # Instructs MLServer to decode inputs into a NumPy array
        },
        "inputs": [
            {
                "name": "predict",
                "shape": [1, 4],     # 1 row, 4 features
                "datatype": "FP32",
                "data": [            # V2 specification expects a flat 1D list
                    float(sepal_l),
                    float(sepal_w),
                    float(petal_l),
                    float(petal_w),
                ]
            }
        ]
    }

    try:
        response = requests.post(KSERVE_URL, json=payload, timeout=10)

        # Cluster/Container logging for debugging
        print("=" * 80)
        print("Target URL :", KSERVE_URL)
        print("Status Code:", response.status_code)
        print("Response   :")
        print(response.text)
        print("=" * 80)

        response.raise_for_status()
        result = response.json()

        # Parse the standard MLServer V2 output wrapper
        if "outputs" in result:
            # Cast to int to ensure it matches the dictionary keys properly
            prediction_index = int(result["outputs"][0]["data"][0])
            
            # Lookup the flower name safely
            flower_name = IRIS_CLASSES.get(prediction_index, "Unknown Species")
            
            return f"Index: {prediction_index} — Flower Name: {flower_name}"

        return f"Unexpected response format:\n{result}"

    except Exception as e:
        return f"Error communicating with predictor:\n{str(e)}"

# Gradio UI Configuration
interface = gr.Interface(
    fn=predict_iris,
    inputs=[
        gr.Number(label="Sepal Length", value=5.1),
        gr.Number(label="Sepal Width", value=3.5),
        gr.Number(label="Petal Length", value=1.4),
        gr.Number(label="Petal Width", value=0.2),
    ],
    outputs=gr.Textbox(label="Prediction Result"),
    title="Iris Production Predictor (KServe Powered)",
)

if __name__ == "__main__":
    interface.launch(server_name="0.0.0.0", server_port=7860)
