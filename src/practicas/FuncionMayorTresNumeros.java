package practicas;

public class FuncionMayorTresNumeros {
    public static void main(String[] args) {

        int num1 = 5;
        int num2 = 8;
        int num3 = 9;

        int mayor = MayorDeTres(num1, num2, num3);

        System.out.println("El mayor es: " + mayor);

    }

    static int MayorDeTres(int a, int b, int c) {
        if (a >= b && a >= c) {
            return a;
        } else if (b >= a && b >= c) {
            return b;
        } else {
            return c;
        }
    }
}
