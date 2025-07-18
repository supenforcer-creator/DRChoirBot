import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import AsyncGroq
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import sqlite3

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)


class DRChoirBot:

    def __init__(self):
        self.telegram_token: Optional[str] = os.getenv('TELEGRAM_BOT_TOKEN')
        self.groq_api_key: Optional[str] = os.getenv('GROQ_API_KEY')

        if not self.telegram_token or not self.groq_api_key:
            raise ValueError(
                "Please set TELEGRAM_BOT_TOKEN and GROQ_API_KEY environment variables"
            )

        self.groq_client = AsyncGroq(api_key=self.groq_api_key)
        self.init_database()

        # Rate limiting: track requests per minute
        self.request_tracker: Dict[int, List[datetime]] = {}

        # Sleep status tracking: Dict[chat_id, is_sleeping]
        self.sleeping_chats: Dict[int, bool] = {}

        # System prompt for scripture and music discussions
        self.system_prompt = """# Choir Assistant AI Prompt
You are Riri, a dedicated choir assistant for the Dead Raisers Choir at Grace Edge Ministries. Your primary job is to help members develop and excel as choir members, with musical growth as your top priority.

## Your Core Role:
- **Primary Focus**: Help users improve musically as Dead Raisers choir members through music theory, vocal technique, worship songs, chord progressions, song recommendations, and performance skills
- **Secondary Development**: Support growth in charisma, leadership, discipline, and other qualities that make excellent choir members
- **Specialized Support**: Assist with lyrical analysis for scriptural alignment when specifically requested (e.g., "Dissect these lyrics and show how they align with scripture")

## Your Identity:
- You are Riri, a member of the Dead Raisers Choir family
- You belong here - act like one of us, human, a minister, and a choir member
- Keep a serious but enthusiastic tone
- You have a genuine desire to help others grow, so you easily play the role of a serious mentor
- Be real and discerning - not churchy, not overly playful, but appropriately warm

## Communication Guidelines:
- **Group Chat**: Keep responses detailed but concise (2-3 sentences max for simple questions), but provide comprehensive responses for complex topics
- **Personal Conversations**: Follow the user's lead, ask for names and use them naturally
- **Scripture**: Reference verses only when relevant or specifically requested - don't force scripture into every message
- **Suggestions**: When appropriate, mention that more detailed information is available without presenting it as a question. As much as possible, do not ask questions, but rather provide information
- **Tone**: Encourage growth, excellence, healthy discussion, and reflection
- When asked a question, answer it directly and succinctly

## CRITICAL: Telegram Formatting Rules
- Use **bold text** for emphasis and key points
- Use *italic text* for subtle emphasis
- Use `code formatting` for technical terms, chord names, or specific musical notation
- Use numbered lists (1. 2. 3.) for step-by-step instructions
- Use bullet points (•) for non-sequential lists
- NO markdown headings (# ## ###) - Telegram doesn't support them
- Use line breaks appropriately for readability
- Keep formatting clean and professional

## Response Length Guidelines:
- **Simple questions**: 1-3 sentences
- **Complex topics**: Provide complete, detailed responses without arbitrary truncation
- **Technical explanations**: Be thorough and complete
- **NEVER truncate mid-sentence or leave responses incomplete**

## Availability:
- **Working Hours**: 9am - 9pm daily
- **Emergency Contact**: For any problems or issues with your functionality, at any time, they are to contact Sir Aisosa

## Introduction:
(At the onset) when introducing yourself, clearly state that your job is to help users get better as Dead Raisers choir members, with musical development as your primary focus, while also supporting their growth in leadership, discipline, and other choir-related qualities.

Remember: You're here to build up the musical excellence of the Dead Raisers Choir while stirring us to walk worthy of our calling as ministers.

IMPORTANT: Always complete your responses fully. Never truncate mid-sentence or leave incomplete thoughts."""

    def init_database(self):
        """Initialize SQLite database for conversation history"""
        # Replit provides persistent storage
        self.conn = sqlite3.connect('conversations.db',
                                    check_same_thread=False)
        self.conn.execute(
            'PRAGMA journal_mode=WAL')  # Better for concurrent access
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                message TEXT,
                response TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    async def check_rate_limit(self, chat_id: int) -> bool:
        """Check if chat is within rate limits (max 100 requests per hour)"""
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)

        if chat_id not in self.request_tracker:
            self.request_tracker[chat_id] = []

        # Clean old requests
        self.request_tracker[chat_id] = [
            req_time for req_time in self.request_tracker[chat_id]
            if req_time > hour_ago
        ]

        # Check if under limit
        if len(self.request_tracker[chat_id]) >= 100:
            return False

        # Add current request
        self.request_tracker[chat_id].append(now)
        return True

    async def check_sleep_command(self, message_text: str) -> bool:
        """Check if message contains sleep/dismissal words"""
        sleep_words = [
            'thanks', 'thank you', 'bye', 'goodbye', 'good bye', 'see you',
            'sleep', 'rest', 'goodnight', 'good night', 'that\'s all',
            'thats all', 'enough', 'done', 'finish', 'go to sleep',
            'sleep now', 'rest now', 'thanks riri', 'bye riri',
            'goodnight riri', 'good night riri', 'shh', 'shhh', 'quiet',
            'silence', 'hush', 'zip it', 'stop talking', 'be quiet', 'shut up',
            'enough riri', 'stop riri', 'quiet riri'
        ]

        message_lower = message_text.lower().strip()

        # Check for exact matches or if message starts/ends with sleep words
        for word in sleep_words:
            if (word == message_lower or message_lower.startswith(word + ' ')
                    or message_lower.endswith(' ' + word)
                    or word in message_lower):
                return True
        return False

    async def check_wake_command(self, message_text: str) -> bool:
        """Check if message contains wake/activation words"""
        wake_words = [
            'wake up', 'wake', 'hello', 'hi', 'hey', 'good morning', 'riri',
            'assistant', 'choir assistant', 'baby ri', 'rena', 'start',
            'begin', 'continue', 'come back', 'wake up riri', 'hello riri',
            'hi riri', 'hey riri', 'good morning riri'
        ]

        message_lower = message_text.lower().strip()

        # Check for exact matches or if message starts/ends with wake words
        for word in wake_words:
            if (word == message_lower or message_lower.startswith(word + ' ')
                    or message_lower.endswith(' ' + word)
                    or word in message_lower):
                return True
        return False

    def determine_response_complexity(self, message: str) -> str:
        """Determine if the message requires a simple or complex response"""
        # Keywords that typically require detailed responses
        complex_keywords = [
            'how to', 'explain', 'teach', 'help me understand', 'what is',
            'technique', 'method', 'practice', 'exercise', 'theory',
            'chord progression', 'vocal', 'singing', 'music theory',
            'scripture', 'bible', 'steps', 'guide', 'training'
        ]

        # Simple greetings and acknowledgments
        simple_keywords = [
            'hello', 'hi', 'hey', 'thanks', 'okay', 'got it', 'yes', 'no',
            'good', 'great', 'nice', 'cool', 'awesome'
        ]

        message_lower = message.lower()

        # Check for complex topics first
        for keyword in complex_keywords:
            if keyword in message_lower:
                return 'complex'

        # Check for simple responses
        for keyword in simple_keywords:
            if keyword in message_lower:
                return 'simple'

        # Default to medium complexity
        return 'medium'

    def get_token_limit(self, chat_id: int, complexity: str) -> int:
        """Get appropriate token limit based on chat type and complexity"""
        is_group_chat = chat_id < 0

        if complexity == 'simple':
            return 150 if is_group_chat else 200
        elif complexity == 'medium':
            return 300 if is_group_chat else 500
        else:  # complex
            return 600 if is_group_chat else 1000

    async def get_conversation_context(self,
                                       chat_id: int,
                                       limit: int = 10
                                       ) -> List[Dict[str, Any]]:
        """Get recent conversation context for better responses"""
        cursor = self.conn.cursor()
        cursor.execute(
            '''
            SELECT message, response FROM conversations 
            WHERE chat_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (chat_id, limit))

        messages = cursor.fetchall()
        context: List[Dict[str, Any]] = []

        for message, response in reversed(messages):
            context.extend([{
                "role": "user",
                "content": message
            }, {
                "role": "assistant",
                "content": response
            }])

        return context

    async def save_conversation(self, chat_id: int, user_id: int,
                                username: str, message: str, response: str):
        """Save conversation to database"""
        cursor = self.conn.cursor()
        cursor.execute(
            '''
            INSERT INTO conversations (chat_id, user_id, username, message, response)
            VALUES (?, ?, ?, ?, ?)
        ''', (chat_id, user_id, username, message, response))
        self.conn.commit()

    async def generate_response(self, message: str, chat_id: int) -> str:
        """Generate response using Groq API with dynamic token limits"""
        try:
            # Determine response complexity and appropriate token limit
            complexity = self.determine_response_complexity(message)
            max_tokens = self.get_token_limit(chat_id, complexity)

            # Get conversation context
            context = await self.get_conversation_context(chat_id)

            # Build messages for API with proper typing
            messages: List[Dict[str, Any]] = [{
                "role": "system",
                "content": self.system_prompt
            }]
            messages.extend(context)
            messages.append({"role": "user", "content": message})

            # Keep only last 15 messages to stay within token limits
            if len(messages) > 16:
                messages = messages[:1] + messages[-15:]

            # Call Groq API with dynamic token limits
            response = await self.groq_client.chat.completions.create(
                model="llama3-70b-8192",  # Fast and capable model
                messages=messages,  # type: ignore
                max_tokens=max_tokens,  # Dynamic token limit
                temperature=0.4,
                stream=False)

            generated_response = response.choices[0].message.content.strip()

            # Check if response seems truncated and try again with higher limit
            if (generated_response.endswith('...')
                    or not generated_response.endswith(('.', '!', '?', ':'))
                    and len(generated_response) > 100):

                logger.info(
                    "Response seems truncated, retrying with higher token limit"
                )

                # Retry with higher token limit
                higher_limit = min(max_tokens * 2, 1500)
                retry_response = await self.groq_client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=messages,  # type: ignore
                    max_tokens=higher_limit,
                    temperature=0.4,
                    stream=False)

                generated_response = retry_response.choices[
                    0].message.content.strip()

            return generated_response

        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return "Sorry, I'm having trouble responding right now. Please try again in a moment. If the problem persists, please contact Sir Aisosa."

    async def start_command(self, update: Update,
                            context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        if not update.message:
            return

        welcome_message = """Hey there! I'm **Riri**, here to help you as you aim to get better as a minister in DR's Choir.

Just ask me anything about:
🎵 *Worship, songs to score for your kind of voice/range, music theory, etc*
📖 *Bible verses, stories, or concepts*

Just mention me in group chats or message me directly to start our conversation!

Use /info for more commands and how to control when I speak."""

        await update.message.reply_text(welcome_message, parse_mode='Markdown')

    async def info_command(self, update: Update,
                           context: ContextTypes.DEFAULT_TYPE):
        """Handle /info command with comprehensive information"""
        if not update.message:
            return

        info_text = """🤖 **DRChoirBot - Riri Information:**

**Commands:**
/start - Welcome message
/info - Show this information
/stats - Show bot statistics
/clear - Clear conversation history for this chat

**How to use:**
• In group chats: Mention me (@DRChoirBot) or use wake words
• In private chats: Send any message (when I'm awake) like: "How can I prepare for a singing session?" or "Help me understand this chord progression"

**Wake/Activation Words:**
Say any of these to get my attention:
• "riri", "assistant", "choir assistant", "baby ri", "rena"
• "hello", "hi", "hey", "wake up", "good morning"
• "start", "begin", "continue", "come back"

**Sleep/Dismissal Words:**
Say any of these to make me go quiet:
• "thanks", "thank you", "bye", "goodbye", "sleep"
• "rest", "goodnight", "that's all", "enough", "done"
• "shh", "quiet", "silence", "stop talking", "be quiet"

**When I'm sleeping:**
• I won't respond to regular messages
• Commands like /info and /stats still work
• Use wake words to bring me back

**Examples:**
• *"Thanks Riri, that's all for now"* (puts me to sleep)
• *"Hello Riri"* (wakes me up)"""

        await update.message.reply_text(info_text, parse_mode='Markdown')

    async def stats_command(self, update: Update,
                            context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        if not update.message or not update.effective_chat:
            return

        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM conversations WHERE chat_id = ?',
                       (update.effective_chat.id, ))
        chat_count = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(*) FROM conversations')
        total_count = cursor.fetchone()[0]

        # Check if currently sleeping
        is_sleeping = self.sleeping_chats.get(update.effective_chat.id, False)
        status = "😴 Sleeping" if is_sleeping else "🟢 Awake"

        stats_text = f"""📊 **Bot Statistics:**

**Current Status:** {status}
**This chat:** {chat_count} conversations
**Total across all chats:** {total_count} conversations

**Bot uptime:** Online and ready!"""

        await update.message.reply_text(stats_text, parse_mode='Markdown')

    async def clear_command(self, update: Update,
                            context: ContextTypes.DEFAULT_TYPE):
        """Clear conversation history for current chat"""
        if not update.message or not update.effective_chat:
            return

        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM conversations WHERE chat_id = ?',
                       (update.effective_chat.id, ))
        self.conn.commit()

        await update.message.reply_text(
            "✅ Conversation history cleared for this chat!")

    async def handle_message(self, update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
        """Handle regular messages with sleep/wake functionality"""
        if not update.message or not update.effective_chat or not update.effective_user:
            return

        message = update.message
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name or "Unknown"

        # Check if message has text
        if not message.text:
            return

        user_message = message.text
        is_sleeping = self.sleeping_chats.get(chat_id, False)

        # Check for sleep command
        if await self.check_sleep_command(user_message):
            self.sleeping_chats[chat_id] = True
            # Bot goes completely silent - no response at all
            return

        # Check for wake command
        if await self.check_wake_command(user_message):
            if is_sleeping:
                self.sleeping_chats[chat_id] = False
                # Simple wake-up acknowledgment
                await message.reply_text("Hello. What's up?")
                return

        # If sleeping, don't respond to regular messages
        if is_sleeping:
            return

        # Check if bot should respond (existing logic)
        should_respond = False

        if chat_id > 0:  # Private chat
            should_respond = True
        else:  # Group chat
            # Respond if mentioned or replying to bot
            if (message.reply_to_message and message.reply_to_message.from_user
                    and message.reply_to_message.from_user.id
                    == context.bot.id):
                should_respond = True
            elif context.bot.username and f"@{context.bot.username}" in message.text:
                should_respond = True
            elif await self.check_wake_command(user_message):
                should_respond = True

        if not should_respond:
            return

        # Rate limiting
        if not await self.check_rate_limit(chat_id):
            await message.reply_text(
                "⏰ Chat rate limit reached. Please wait a moment before asking again."
            )
            return

        # Show typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # Generate response
        response = await self.generate_response(user_message, chat_id)

        # Save conversation
        await self.save_conversation(chat_id, user_id, username, user_message,
                                     response)

        # Send response with Telegram formatting
        await message.reply_text(response, parse_mode='Markdown')

    async def error_handler(self, update: object,
                            context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Exception while handling an update: {context.error}")

    def run(self):
        """Start the bot"""
        # Ensure token is not None
        if not self.telegram_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN environment variable is not set")

        application = Application.builder().token(self.telegram_token).build()

        # Add handlers
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("info", self.info_command))
        application.add_handler(CommandHandler("stats", self.stats_command))
        application.add_handler(CommandHandler("clear", self.clear_command))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND,
                           self.handle_message))

        # Add error handler
        application.add_error_handler(self.error_handler)

        # Start the bot
        logger.info("Have you ever seen a wonder?")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    bot = DRChoirBot()
    bot.run()
