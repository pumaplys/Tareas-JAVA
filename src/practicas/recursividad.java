package practicas;

public class recursividad {
    public static void main(String[] args) {
        int factorial5 = factorial(5);
        System.out.println("El factorial es: " + factorial5);
    }

    static int factorial(int numero) {
        if (numero == 1) {
            return 1;
        } else {
            return numero * factorial(numero -1);
        }
    }
}
