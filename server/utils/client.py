from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from utils.constants import GEMINI_FLASH_LITE_MODEL
import traceback
from pydantic import BaseModel

class LLMClient:
    def __init__(self):
        self.client = genai.Client()
        self.config = {
            "max_output_tokens": 1000,  
        }
        
    
    def generate_response(self, prompt: str, schema_name: str, schema: BaseModel, model: str = None, system_prompt: str = None, temperature: float = 1.0, file=None, filepath: str = None) -> BaseModel:
        try:
            return self._gemini_structured_response(prompt, schema, model, system_prompt=system_prompt, temperature=temperature, file=file)
        except genai_errors.ServerError as e:
            traceback.print_exc()
            raise Exception("Something went wrong")

    async def stream_response(self, prompt: str, model: str = None, system_prompt: str = None, temperature: float = 1.0):
        """ Stream plain-text deltas from Gemini as they're generated """
        config = types.GenerateContentConfig(**self.config, temperature=temperature)
        if system_prompt:
            config["system_instruction"] = system_prompt
        # no response_json_schema here since we want raw text, not structured output
        # notice it uses the aio, which is the async client
        # returns an iterator
        stream = await self.client.aio.models.generate_content_stream(
            model=model or GEMINI_FLASH_LITE_MODEL,
            contents=[prompt],
            config=config,
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text

    def _gemini_structured_response(self, prompt: str, schema: BaseModel, model: str = None, system_prompt: str = None, temperature: float = 1.0, file=None) -> dict:
        """
        Uses gemini to create structured response that is validated by json schema
        """
        try:
            config = types.GenerateContentConfig(
                **self.config,
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
                temperature=temperature,
            )
            if system_prompt:
                config["system_instruction"] = system_prompt
            contents = [prompt, file] if file else [prompt]
            response = self.client.models.generate_content(
                model=model or GEMINI_FLASH_LITE_MODEL,
                contents=contents,
                config=config,
            )
            validated_schema = schema.model_validate_json(response.text)
            return validated_schema
        except genai_errors.ServerError:
            raise
        except Exception as e:
            traceback.print_exc()
            raise Exception("Something went wrong")

