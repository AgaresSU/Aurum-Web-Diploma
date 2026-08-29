from django.shortcuts import render



def brief(request):
    return render(request, 'leads/brief.html')
