# 📚 Study Notes Assistant

A simple web app built with Python and Streamlit. Upload a PDF or paste your lecture notes, and the app generates a **summary** and **multiple-choice quiz questions** with an answer key, using an AI model from [NVIDIA Build](https://build.nvidia.com) through its OpenAI-compatible API.

## Features

- Upload a text-based PDF or paste your notes directly
- Generates a concise bullet-point summary
- Generates 3 to 15 multiple-choice questions (A-D) with a hidden answer key
- Responses stream onto the screen as they are written
- API key is stored in a local `.env` file, never in the code

## Requirements

- Python 3.9 or newer
- A free NVIDIA Build API key (get one at [build.nvidia.com](https://build.nvidia.com))

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/barisoztrk24/ders-aracim.git
   cd ders-aracim
   ```

2. Create and activate a virtual environment:

   ```bash
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # macOS / Linux
   ```

3. Install the dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Create a file named `.env` in the project folder and add your API key:

   ```
   NVIDIA_API_KEY=your_api_key_here
   ```

   You can use `.env.example` as a template. Never share or commit your real `.env` file.

## Usage

```bash
streamlit run app.py
```

The app opens in your browser (usually at `http://localhost:8501`). Upload a PDF or paste your notes, choose the number of questions, and click **Generate**.

## Changing the AI model

The model name is set in `app.py`:

```python
MODEL = "meta/muse-glimmer-30b"
```

You can replace it with any chat model available on [build.nvidia.com](https://build.nvidia.com). Copy the exact name from the `model=` line in the model's code example. Models that "think" before answering may respond more slowly.

## Limitations

- Only PDFs that contain selectable text are supported. Scanned PDFs (images of pages) cannot be read.
- Very long notes are cut off at 20,000 characters to keep responses fast. Split long documents into sections for better results.
- AI-generated summaries and questions can contain mistakes. Always check them against your original notes.

## Project structure

```
ders-aracim/
├── app.py              # The Streamlit app
├── requirements.txt    # Python dependencies
├── .env.example        # Template for the API key file
├── .gitignore          # Keeps .env and venv out of Git
└── README.md
```

## Privacy

Your notes are sent to NVIDIA's API to generate the results. Do not upload confidential or personal information.