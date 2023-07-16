import sys

import flags
import os
import typing

import datasets
import torch
import transformers

import data
from trainer import RandomizedIndividualCalibrationTrainer
from torch import nn
from transformers import TrainingArguments, Trainer, BertForSequenceClassification, AutoTokenizer
from datasets import load_dataset

import model


# To run this script using Newton, run the following command:
# srun --partition=nlp --account=nlp --gres=gpu:1 -c 10 python main.py
# The above command will request 1 GPU and 10 CPU cores. You can change these values as needed.
# Note that you will need to adjust the partition and account to match your Newton account.


# TODO: Create a custom Trainer class that overrides the compute_loss method. This is where we will implement the
#  individualized calibration loss. Once this is done, we can use this Trainer class (instead of the default Trainer) to
#  train the model.
# class RandomizedIndividualizedForecasterTrainer(Trainer):
#     def compute_loss(
#             self,
#             model: nn.Module,
#             inputs: typing.Union[typing.Dict[str, torch.Tensor], typing.Tuple[torch.Tensor]],
#             return_outputs: bool = False,
#     ) -> typing.Union[float, typing.Tuple[float, torch.Tensor]]:
#         labels = inputs.get("labels")
#         # forward pass
#         outputs = model(**inputs)
#         logits = outputs.get("logits")
#         # compute custom loss (suppose one has 3 labels with different weights)
#         loss_fct = nn.CrossEntropyLoss()
#         loss = loss_fct(logits.view(-1, self.model.config.num_labels), labels.view(-1))
#         return (loss, outputs) if return_outputs else loss


def main():
    # Parse arguments
    parser = transformers.HfArgumentParser(
        (flags.ModelArguments, flags.DataArguments, flags.TrainingArguments)
    )
    model_args, data_args, training_args = parser.parse_args_into_dataclasses()
    print(f'model args are:\n{model_args}')
    print(f'data args are:\n{data_args}')
    print(f'training args are:\n{training_args}')
    # args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(model_args.model_name_or_path)

    # TODO: Load the dataset from tensorflow_datasets as opposed to the huggingface dataset.
    # dataset = load_dataset("civil_comments")

    def tokenizer_function(
            example: typing.Dict[str, typing.Any],
    ) -> typing.Dict[str, typing.Any]:
        """
        Tokenize the input text and return the result (including the original label).
        :param example: A dictionary containing the input text and the label.
        :return: A dictionary containing the tokenized input text and the label.
        """
        result = tokenizer(
            example[data_args.dataset_features_column],
            truncation=True,                # Truncate the input sequences to the maximum length the model accepts.
            padding='max_length',           # Pad the input sequences to the maximum length the model accepts.
            max_length=512,                 # The maximum sequence length of the input text for BERT is 512 tokens.
            return_tensors='pt',            # Return the result as PyTorch tensors.
        )
        # Add the labels to the result. This is required by the Trainer.
        result['labels'] = example[data_args.dataset_labels_column]
        return result

    # # Split the dataset into training, validation, and test sets.
    # # Ensure that their contents are conducive to the selected language model.
    # training_set = tokenized_dataset['train']
    # tokenized_dataset.set_format(
    #     type='torch',
    #     columns=['input_ids', 'token_type_ids', 'attention_mask', 'labels'],
    # )
    # validation_set = tokenized_dataset['validation']
    # tokenized_dataset.set_format(
    #     type='torch',
    #     columns=['input_ids', 'token_type_ids', 'attention_mask', 'labels'],
    # )
    # test_set = tokenized_dataset['test']
    # tokenized_dataset.set_format(
    #     type='torch',
    #     columns=['input_ids', 'token_type_ids', 'attention_mask', 'labels'],
    # )

    tokenized_train_dataset_name = 'tokenized_train_data.csv'
    tokenized_validation_dataset_name = 'tokenized_eval_data.csv'
    tokenized_test_dataset_name = 'tokenized_test_data.csv'

    # Check if the tokenized datasets already exist. If so, load them. Otherwise, create them.
    datasets_exist = (
        os.path.exists(os.path.join('data', tokenized_train_dataset_name)) and
        os.path.exists(os.path.join('data', tokenized_validation_dataset_name)) and
        os.path.exists(os.path.join('data', tokenized_test_dataset_name))
    )

    if datasets_exist:
        train_dataset = datasets.load_from_disk(os.path.join('data', tokenized_train_dataset_name))
        validation_dataset = datasets.load_from_disk(os.path.join('data', tokenized_validation_dataset_name))
        test_dataset = datasets.load_from_disk(os.path.join('data', tokenized_test_dataset_name))

    # Load datasets
    else:
        train_dataset, validation_dataset, test_dataset = data.create_dataloaders(
            train_file=data_args.train_file,
            validation_file=data_args.validation_file,
            test_file=data_args.test_file,
            tokenizer_function=tokenizer_function,
            batch_size=model_args.tokenizer_batch_size,
        )
        train_dataset.save_to_disk(
            os.path.join('data', tokenized_train_dataset_name),
        )
        validation_dataset.save_to_disk(
            os.path.join('data', tokenized_validation_dataset_name),
        )
        test_dataset.save_to_disk(
            os.path.join('data', tokenized_test_dataset_name),
        )

    # Load pretrained model
    # We load the model from the checkpoint specified by the model_name_or_path argument such that the model is
    # initialized with the same weights as the pretrained model.
    # We set the number of labels to 1, since that is conducive to regression (the case for predicting toxicity in the
    # range [0, 1]).
    randomized_individual_calibration_model = model.CommentRegressor(
        mlp_hidden=model_args.mlp_hidden,
        drop_prob=model_args.mlp_dropout,
        text_encoder_model_name=model_args.model_name_or_path,
    )

    # model = BertForSequenceClassification.from_pretrained(
    #     model_args.model_name_or_path,
    #     num_labels=1,
    # )

    # TODO: Initialize logging to wandb. The trainer should report to wandb.

    # Define training arguments
    training_args = TrainingArguments(
        run_name="civil_comments_bert",  # TODO: Make this a flag.
        output_dir=training_args.output_dir,
        load_best_model_at_end=True,
        remove_unused_columns=False,
        evaluation_strategy="steps",
        eval_steps=training_args.eval_steps,  # Evaluate every 100 steps.
        save_strategy="steps",
        save_steps=training_args.save_steps,  # Save checkpoint every 1000 steps.
        num_train_epochs=training_args.num_train_epochs,
        per_device_train_batch_size=training_args.per_device_train_batch_size,
        per_device_eval_batch_size=training_args.per_device_eval_batch_size,
        learning_rate=training_args.learning_rate,
        seed=training_args.training_seed,
        data_seed=data_args.data_seed,
        optim=training_args.optimizer,
        lr_scheduler_type=training_args.lr_scheduler_type,
        warmup_ratio=training_args.warmup_ratio,
        weight_decay=training_args.weight_decay,
        logging_dir="./logs",
        logging_steps=training_args.logging_steps,  # Log every 10 steps.
        # Parameters to increase the efficiency of the training.
        fp16=True,
        dataloader_num_workers=training_args.dataloader_num_workers if torch.cuda.is_available() else 0,
    )

    # TODO: Make the compute_metrics function compute accuracy and MSE on the basis of the CDF which the model produces.
    # TODO: Add fairness metrics to the compute_metrics function (e.g., TPR, FPR, BPSN, BNSP, AUC, etc...)
    # def compute_metrics(
    #         eval_pred: transformers.EvalPrediction,
    # ) -> dict[str, float]:
    #     """Compute metrics for the evaluation predictions.
    #
    #     :param eval_pred: The output of the model on the evaluation set.
    #     :return: A dictionary containing the metrics.
    #     """
    #     labels = eval_pred.label_ids
    #     preds = eval_pred.predictions.argmax(-1)
    #     acc = (preds == labels).mean()
    #     mean_squared_error = ((preds - labels) ** 2).mean()
    #     metrics = {
    #         "accuracy": acc,
    #         "mse": mean_squared_error,
    #     }
    #     return metrics

    # TODO: Need to separate into train/validation/test sets in the future.
    # Initialize our Trainer

    trainer = RandomizedIndividualCalibrationTrainer(
        model=randomized_individual_calibration_model,
        args=training_args,
        # compute_metrics=compute_metrics,
        train_dataset=train_dataset['train'],
        eval_dataset=validation_dataset['train'],
        # train_dataset=training_set.remove_columns(["text", "toxicity"]),    # Remove the text and toxicity columns.
        # eval_dataset=validation_set.remove_columns(["text", "toxicity"]),   # Remove the text and toxicity columns.
        callbacks=[
            transformers.EarlyStoppingCallback(early_stopping_patience=3),
        ],
    )

    # Training
    trainer.train()

    # Evaluation
    eval_result = trainer.evaluate(eval_dataset=test_dataset['train'])
    print(f"Eval result: {eval_result}")


if __name__ == "__main__":
    main()
