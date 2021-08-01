def Log(*args, **kwargs):
    """
    记录Log信息
    """
    if kwargs:
        print("logger Log: ", *args, kwargs)
    else:
        print("logger Log: ", *args)
