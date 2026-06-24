import os
import shutil
import mlflow
import mlflow.sklearn
import pandas as pd

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split


def run_pipeline():
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI"))
    mlflow.set_experiment("iris-gitops-pipeline")

    df = pd.read_csv("data/iris.csv")

    X = df.drop(columns=["target"])
    y = df["target"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    with mlflow.start_run() as run:

        model = RandomForestClassifier(
            n_estimators=50,
            random_state=42
        )

        model.fit(X_train, y_train)

        predictions = model.predict(X_test)
        accuracy = accuracy_score(y_test, predictions)

        mlflow.log_param("n_estimators", 50)
        mlflow.log_metric("accuracy", accuracy)

        if accuracy < 0.85:
            raise Exception(f"Accuracy gate failed ({accuracy:.3f})")

        mlflow.sklearn.log_model(
            sk_model=model,
            artifact_path="model",
            registered_model_name="iris-prod-model"
        )

        export_dir = "exported_model"

        if os.path.exists(export_dir):
            shutil.rmtree(export_dir)

        mlflow.sklearn.save_model(
            sk_model=model,
            path=export_dir
        )

        with open("run_id.txt", "w") as f:
            f.write(run.info.run_id)

        print(f"Run ID : {run.info.run_id}")
        print(f"Accuracy : {accuracy:.4f}")
        print(f"Model exported to {export_dir}")


if __name__ == "__main__":
    run_pipeline()
