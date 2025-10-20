package tema2.t01;

import java.util.Scanner;

public class ej04 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Ingrese un numero");
        double n1=sc.nextDouble();
        System.out.println("Ingrese otro numero");
        double n2=sc.nextDouble();

        if (n2 != 0){
            System.out.println(n1 + "/" + n2 + " da como resultado: " + (n1/n2));
        } else
        {
            System.out.println("No se puede dividir entre cero");
        }
    }
}
