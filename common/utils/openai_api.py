from openai import OpenAI
import os
import urllib.request
from common.utils.cdn import upload_to_cdn
from dotenv import load_dotenv
load_dotenv()
import re

# Add logging
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
def summarize_text(text, max_tokens=100):
    text = "Summarize this text as a news article title:\n" + text
    openai_request = client.chat.completions.create(
        model="gpt-3.5-turbo-1106",
        messages=[
            {
                "role":"system",
                "content":"You are a news writer summarizing a news article as a single title that is less than 10 words."                
            },
            {
                "role":"user",
                "content":text
            },
            {
                "role":"system",
                "content":"The title of the article with no quotes and no other characters like backslashes surrounding it is:"
            }
        ],         
    )
    print(openai_request.choices[0].message)
    return openai_request.choices[0].message

def preprocess_text_for_image_generation(text):
    """
    Preprocesses text to avoid triggering text/letter generation in AI images.
    Replaces text-related words with visual alternatives.
    """
    
    # Dictionary of problematic words and their visual alternatives
    text_trigger_replacements = {
        # Text/writing related
        'text': 'visual elements',
        'word': 'concept',
        'words': 'concepts',
        'letter': 'symbol',
        'letters': 'symbols',
        'writing': 'marks',
        'write': 'create',
        'written': 'expressed',
        'font': 'style',
        'typography': 'visual design',
        
        # Media with text
        'sign': 'marker',
        'signs': 'markers',
        'poster': 'artwork',
        'billboard': 'display',
        'banner': 'fabric',
        'newspaper': 'printed material',
        'magazine': 'publication',
        'book': 'bound object',
        'books': 'bound objects',
        'document': 'paper',
        'page': 'surface',
        'pages': 'surfaces',
        'menu': 'list display',
        'label': 'tag',
        'labels': 'tags',
        'ticket': 'pass',
        'receipt': 'paper slip',
        
        # Digital/screen text
        'screen': 'display surface',
        'monitor': 'display',
        'computer': 'electronic device',
        'phone': 'device',
        'website': 'digital interface',
        'app': 'application interface',
        
        # Communication
        'message': 'communication',
        'email': 'correspondence',
        'chat': 'conversation',
        'note': 'reminder',
        'notes': 'reminders',
        
        # Educational
        'homework': 'school work',
        'essay': 'composition',
        'report': 'document',
        'study': 'learning',
        'exam': 'test',
        'quiz': 'assessment'
    }
    
    # Convert to lowercase for matching, but preserve original case in replacement
    processed_text = text
    
    for trigger_word, replacement in text_trigger_replacements.items():
        # Case-insensitive replacement while preserving case pattern
        pattern = re.compile(re.escape(trigger_word), re.IGNORECASE)
        processed_text = pattern.sub(replacement, processed_text)
    
    # Remove any remaining references to specific text content
    # Replace quoted text with generic descriptions
    processed_text = re.sub(r'"[^"]*"', 'quoted content', processed_text)
    processed_text = re.sub(r"'[^']*'", 'referenced content', processed_text)
    
    # Replace any remaining text-suggesting phrases
    text_phrases = [
        'says', 'said', 'reads', 'reading', 'spell', 'spelling',
        'caption', 'title', 'headline', 'subtitle'
    ]
    
    for phrase in text_phrases:
        pattern = re.compile(r'\b' + re.escape(phrase) + r'\b', re.IGNORECASE)
        processed_text = pattern.sub('shows', processed_text)
    
    return processed_text

def generate_and_save_image_to_cdn(directory, text):
    logger.info(f"Generating image for text: {text}")
    processed_text = preprocess_text_for_image_generation(text)
    logger.info(f"Processed text for image generation: {processed_text}")
    
    prompt = f"""Create a purely visual artistic image with absolutely no text, letters, words, numbers, symbols, or written characters of any kind. 
        Style: Choose from abstract art, impressionist painting, digital art, watercolor, or detailed sketch.
        Quality: High detail, 4K resolution, professional quality.
        Content: Visual interpretation of: {processed_text}
        Focus: Colors, shapes, textures, lighting, composition, and mood only.
        Important: Zero text, lettering, or readable characters anywhere in the image."""

    logger.info(f"Generating image with prompt: {prompt}")

    openai_response = client.images.generate(
        model="dall-e-3",  # Updated to use DALL-E 3 (gpt-image-1 might be outdated)
        prompt=prompt,
        n=1,
        size="1024x1024"       
    ) 

    image_url = openai_response.data[0].url
    
    # Create a short filename from input text
    filename = text.replace(" ", "_").replace(".", "").replace(",", "").replace("?", "").replace("!", "").replace(":", "").replace(";", "").replace("(", "").replace(")", "").replace("\"", "").replace("\'", "").replace("/", "").replace("\\", "")
    # Make sure filename is less than 100 characters
    filename = filename[:100]
    # Add .png to filename
    filename = f"{filename}.png"

    print(f"Saving to {filename}...")
    urllib.request.urlretrieve(image_url, filename)
    
    print(f"Saving to CDN: {directory}/{filename}...")
    
    # Save image to CDN
    upload_to_cdn(directory, filename)

    print("Deleting...")
    # Delete filename
    os.remove(filename)

    return filename

