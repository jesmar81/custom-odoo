{
    'name': 'AI WhatsApp Tools',
    'version': '19.0.1.0.0',
    'category': 'Hidden',
    'summary': "Tools for any AI agent to read WhatsApp conversations and propose messages that a person approves",
    'description': """
Any agent can use these tools: purchases, sales, collections, support...

* find and read conversations,
* propose a message: it is stored as a DRAFT in WhatsApp > Pending approval,
* nothing is ever sent by the agent: a person approves, then the queue sends it spaced and within limits.

The tools run with the access rights of the user talking to the agent, so they only see the
accounts and conversations that user can see.
""",
    'author': 'jesmar81',
    'depends': ['ai_app', 'ai_order_tools', 'evolution_api'],
    'data': [
        'data/ir_actions_server_tools.xml',
        'data/ai_topic.xml',
        'data/ai_agent_data.xml',
    ],
    'license': 'LGPL-3',
}
