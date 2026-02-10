
---

# 📄 RAG-PDF: Multimodal Document Intelligence
[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![LangChain](https://img.shields.io/badge/LangChain-0.1-blue)](https://www.langchain.com/)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-green)](https://ollama.ai/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-Vector%20DB-yellow)](https://www.trychroma.com/)
[![OpenCLIP](https://img.shields.io/badge/OpenCLIP-Multimodal-red)](https://github.com/mlfoundations/open_clip)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**RAG-PDF** is a high-performance, multimodal Retrieval-Augmented Generation (RAG) pipeline designed to extract, process, and query complex PDF documents. Unlike standard text-based RAG, this system treats images and text as first-class citizens, enabling precise visual and textual information retrieval.

---

## ✨ Key Features

### 🖼️ Multimodal Processing

* **Smart Image Extraction**: Uses `PyMuPDF` to extract high-quality images while automatically filtering out low-variance (flat) or mostly white images.
* **Visual Context Mapping**: Links images to their surrounding text blocks and captions based on spatial coordinates (bounding boxes).
* **OpenCLIP Integration**: Embeds both images and their associated captions into a unified vector space for semantic visual search.

### 🧩 Advanced Chunking & Retrieval

* **BBox-Aware Chunking**: A custom strategy that groups text blocks based on vertical gaps and character limits to preserve document structure.
* **Dual Vector Storage**: Separate high-dimensional indexing for text (using `BGE` embeddings) and images (using `OpenCLIP`).
* **Cross-Encoder Reranking**: Utilizes `ms-marco-MiniLM` to rerank retrieved text chunks, ensuring maximum relevance before passing to the LLM.

### 🤖 Local AI Execution

* **Ollama Backend**: Fully local execution of large language models (defaulting to `gemma3:4b`).
* **Strictly Factual Responses**: A specialized prompt engineering layer that forces the model to rely solely on provided document context, explicitly citing when information is missing.

---

## 🛠 Technology Stack

| Component | Technologies |
| --- | --- |
| **Framework** | LangChain |
| **LLM Engine** | Ollama (Local) |
| **Embeddings** | BAAI/bge-base-en-v1.5 (Text), OpenCLIP ViT-B-32 (Vision) |
| **Vector DB** | ChromaDB (Persistent Storage) |
| **Reranker** | Sentence-Transformers Cross-Encoder |
| **PDF Engine** | PyMuPDF (fitz) |
| **Data Science** | NumPy, PyTorch, PIL |

---

## 🚀 Quick Start

### Prerequisites

* Python 3.9+
* Ollama installed and running
* NVIDIA GPU (optional, but recommended for embeddings)

### Installation

1. **Clone & Install Dependencies**

```bash
git clone https://github.com/Rakshak-D/rag-pdf.git
cd rag-pdf
pip install -r requirements.txt

```

2. **Configure Local LLM**
Ensure Ollama is running and pull the required model:

```bash
ollama pull gemma3:4b

```

3. **Run the Pipeline**
The core logic is contained within the `notebooks/` directory. You can run the full pipeline via:

```bash
jupyter notebook notebooks/rag_pipeline.ipynb

```

---

## 📁 Project Structure

```text
rag-pdf/
├── data/
│   ├── pdf/                # Target PDF documents
│   ├── images_pymupdf/     # Extracted images sorted by document
│   └── database/           # Persistent ChromaDB storage
├── notebooks/
│   ├── 01_load_document.ipynb
│   ├── 02_chunking.ipynb
│   ├── 03_embedding.ipynb
│   ├── 04_vector_store.ipynb
│   ├── 05_retrieval.ipynb
│   ├── 06_llm.ipynb
│   └── rag_pipeline.ipynb   # Complete integrated pipeline
└── requirements.txt         # Project dependencies

```

---

## 🔄 Pipeline Workflow

### 1. Document Ingestion

* Load PDFs using `PyMuPDF`.
* Extract text blocks with precise X/Y coordinates.
* Extract and save images, generating metadata that links them to specific pages.

### 2. Multi-Vector Indexing

* **Text**: Embedded using `BGE` and stored in the text collection.
* **Vision**: Images and captions are fused (weighted 70/30) and stored in the image collection.

### 3. Contextual Retrieval

* User query is embedded by both models.
* Top *k* text chunks and images are retrieved.
* Text is reranked for semantic accuracy.

### 4. Grounded Generation

* LLM receives a formatted context containing text, image captions, and the raw images (via Base64).
* Result is generated with a focus on evidence-based conclusions.

---

## 📄 License

This project is licensed under the MIT License.