{
    'name': 'AI Custom Providers',
    'version': '19.0.1.0.0',
    'category': 'Hidden',
    'author': 'jesmar81',
    'summary': "Connect Odoo AI agents to any OpenAI-compatible provider (Fireworks AI, Groq, DeepSeek, ...)",
    'depends': ['ai', 'ai_app'],
    'data': [
        'security/ir.model.access.csv',
        'views/ai_llm_connection_views.xml',
        'data/ai_llm_connection_data.xml',
    ],
    'license': 'LGPL-3',
}
