"""
Phase 1 — building the tool-calling loop by hand.

  part 1: send a prompt + tool definitions and SEE the tool call come back.   ✅ done
  part 2: actually RUN the tool and feed the result back so the model can      ← this file now
          finish the answer.

This is a throwaway learning script, not part of the app. It still does NOT
loop yet (that's the iteration-capped loop, a later step) — it does exactly one
round trip: ask → tool call → run it → send result → final text.

Run it from the server/ directory:
    python scratch_tools.py
"""
from dotenv import load_dotenv

# Load GEMINI_API_KEY into os.environ before genai.Client() reads it.
load_dotenv()

from google import genai
from google.genai import types
from utils.constants import GEMINI_FLASH_LITE_MODEL
from datetime import datetime
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# 1. THE TOOL DEFINITION  ← what the model SEES (a description, not code)
# ---------------------------------------------------------------------------
get_current_time_decl = types.FunctionDeclaration(
    name="get_current_time",
    description="When the user asks what the current time is, call this function. No need to ask for timezone.",
    parameters=types.Schema(type=types.Type.OBJECT, properties={
        "timezone": {
            "type": types.Type.STRING,
            "description": "current timezone, optional parameter. If the user didn't specify, use UTC"
        }
    })
)


# ---------------------------------------------------------------------------
# 1b. THE TOOL IMPLEMENTATION  ← what the model NEVER sees (the real Python)
#
# The definition above and this function are two separate things. The model
# only ever read the definition; now you write the code that actually runs.
#
# TODO: return the current time as a string.
#   - signature is already `timezone="UTC"` so a missing arg still works.
#   - hint: datetime.now(ZoneInfo(timezone)) gives you an aware datetime;
#     .strftime("%Y-%m-%d %H:%M:%S %Z") or .isoformat() makes it a tidy string.
#   - return a plain str — that's what we'll hand back to the model.
# ---------------------------------------------------------------------------
def get_current_time(timezone: str = "UTC") -> str:
    return datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S %Z")


# A tiny "tool registry": name -> Python callable. With one tool this is
# overkill, but it's the exact pattern Phase 4 grows into. Dispatch looks the
# tool up here by the name the model gave us.
TOOLS = {
    "get_current_time": get_current_time,
}


def main():
    client = genai.Client()

    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=[get_current_time_decl])],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        max_output_tokens=1000,
    )

    # The conversation history. It's now a real list we'll GROW, not a bare
    # string — because feeding the tool result back means appending more turns
    # and calling the model again with the whole history.
    contents = [
        types.Content(role="user", parts=[types.Part(text="What time is it right now?")]),
    ]

    MAX_ITERS = 6
    model_turn = None
    for i in range(MAX_ITERS):
        # --- first call: model decides to ask for a tool ------------------------
        response = client.models.generate_content(
            model=GEMINI_FLASH_LITE_MODEL,
            contents=contents,
            config=config,
        )

        # is there a tool call?
        model_turn = response.candidates[0].content
        # in the future, its possible that we do parallel tool calls,
        # which means this would need to be a list instead to hold all function calls
        function_call = None
        for part in model_turn.parts:
            if part.function_call:
                print(f"function call: name {part.function_call.name} args: {part.function_call.args}")
                function_call = part.function_call
            elif part.text:
                print(f"text:  {part.text}")

        if function_call is None:
            print("No tool call - model answered directly, nothing to run.")
            return

        # -----------------------------------------------------------------------
        # 2a. DISPATCH  ← run the real Python function the model asked for
        #
        # function_call.name is a str ("get_current_time").
        # function_call.args is a dict ({} or {"timezone": "UTC"}).
        #
        # TODO:
        #   - look the function up in TOOLS by function_call.name
        #   - call it, unpacking the args dict as keyword arguments (the ** splat):
        #       result = fn(**function_call.args)
        #   - print the result so you can see it
        # -----------------------------------------------------------------------
        # TODO: dispatch to the right tool and capture its return value
        result = TOOLS[function_call.name](**function_call.args)
        print(f"{function_call.name} result: {result}")

        # -----------------------------------------------------------------------
        # 2b. FEED THE RESULT BACK  ← the subtle, important part
        #
        # To let the model turn "15:42:00 UTC" into a sentence, call it AGAIN with
        # the history extended by TWO turns:
        #   (1) the model's own turn that contained the function_call, so the
        #       function response isn't "dangling":
        #           contents.append(response.candidates[0].content)
        #   (2) a new turn carrying your result back. In google-genai the function
        #       response rides in a Part built by from_function_response:
        #           fr_part = types.Part.from_function_response(
        #               name=function_call.name,
        #               response={"result": result},
        #           )
        #           contents.append(types.Content(role="user", parts=[fr_part]))
        #
        # TODO: append those two turns to `contents`.
        # -----------------------------------------------------------------------
        # TODO (2b): append the model turn, then append the function-response turn
        # you need to do this so the model "knows" that you made a request for a tool call,
        # and then you're now including BOTH the response from the LLM that mentions the tool call (called the "model turn")
        # AND the result of the tool call that was calculated locally here
        # when we're in the loop, after collect both parts, since this is stored in content,
        # in the following iteration of the loop, it should return plain text in this case
        # note for future reference, it's possible model_turn can hold text + a function call together,
        # or multiple function calls in one turn, so it's best to just pass in the returned variable as is.
        contents.append(model_turn)
        fr_part = types.Part.from_function_response(
            name=function_call.name,
            response={"result": result}
        )
        contents.append(types.Content(role="user", parts=[fr_part]))

    print(f"Hit the {MAX_ITERS}-iteration cap without a final answer.")

if __name__ == "__main__":
    main()
